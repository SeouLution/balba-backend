# 파일, 엑셀 읽기, 영수증 읽기, 외부 서비스

외부 연동은 전부 실제 서비스를 쓴다(D24). 아래 표의 공급자는 확정이다. 각 서비스의 요청·응답 필드 이름은 이 문서에 적지 않았다. adapter를 구현할 때 공식 문서를 확인하고, 실제 응답을 하나 받아 `tests/fixtures/providers/`에 저장한 뒤 그 파일로 매핑 테스트를 쓴다. 기억에 의존해 필드 이름이나 URL을 적지 않는다.

## 공급자

| 용도 | 공급자 | 환경변수 | 확인할 공식 문서 |
|---|---|---|---|
| 엑셀 열 판별, 영수증 읽기, 레시피 초안 | Claude API (`anthropic` Python SDK) | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`(기본 `claude-opus-5-5`) | platform.claude.com 문서의 Vision, Structured outputs |
| 주소 → 좌표 | 카카오 로컬 API (주소 검색) | `KAKAO_REST_API_KEY` | developers.kakao.com 로컬 API |
| 날씨 | 기상청 단기예보 조회서비스 | `KMA_SERVICE_KEY` | 공공데이터포털 data.go.kr의 활용가이드(격자 변환 식 포함) |
| 행사 일정·위치 | 서울시 문화행사 정보 | `SEOUL_OPENAPI_KEY` | 서울 열린데이터광장 OA-15486 |
| 행사장 혼잡도·붐비는 시간 | 서울 실시간 도시데이터 | `SEOUL_OPENAPI_KEY`(별도 키가 필요하면 `SEOUL_CITYDATA_KEY`) | 서울 열린데이터광장 OA-21285의 API 매뉴얼 |
| 푸시 | 표준 웹 푸시(VAPID), `pywebpush` | `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_SUBJECT` | RFC 8292, pywebpush README |

공통 규칙: HTTP timeout은 connect 3초/read 15초(Claude API는 SDK 기본값). 429와 5xx만 adapter 안에서 최대 2번 다시 시도한다. 그래도 실패하면 `Unavailable`을 돌려주고 작업 재시도([작업 명세](jobs.md))에 맡긴다. 외부에서 받은 문자열은 데이터로만 다루고 지시로 실행하지 않는다. 키, 푸시 구독 정보, 전화번호 전체, 영수증 이미지는 로그에 남기지 않는다.

## Claude API 사용 규칙

- 공식 SDK(`anthropic`)의 `AsyncAnthropic`을 쓴다. 모델은 `ANTHROPIC_MODEL`이고 기본값은 `claude-opus-5-5`다.
- JSON이 필요한 호출은 모두 Structured outputs(`output_config.format`, SDK의 `messages.parse()`)로 스키마를 강제한다. 응답 글에서 JSON을 잘라내지 않는다. 특정 도구 호출을 강제하는 `tool_choice`는 이 모델에서 지원하지 않으므로 쓰지 않는다.
- 응답의 `stop_reason`이 `refusal`이나 `max_tokens`면 결과를 쓰지 않고 `InvalidPayload`로 처리한다. SDK 문서의 refusal fallback 기본 설정을 켠다.
- 이미지는 base64 JPEG 이미지 블록으로 보낸다.
- 테스트에서는 포트를 fake로 바꾼다. 실제 API를 부르는 테스트는 기본 실행에 넣지 않는다.

## 파일 저장

`FileStore.put(stream, limits) → FileRef`, `open(FileRef) → stream`. 저장 위치는 `FILE_STORAGE_ROOT` 아래 `<storeId>/<종류>/<uuid>`다. 클라이언트가 보낸 파일명은 위치에 쓰지 않는다. 파일을 내려주는 공개 엔드포인트는 없다.

한도는 [정책 파일](../../../app/policies/defaults.toml) `[upload]`를 따른다. 확장자, 실제 파일 시그니처, 크기를 함께 검사한다. 판매 파일은 xlsx/xls/csv, 영수증은 JPEG 1~30장이다. 파일을 먼저 저장한 뒤 DB 접수 트랜잭션을 연다. DB가 실패해 남은 파일은 참조가 없는 파일이므로 운영자가 지워도 된다.

## 엑셀 읽기 (D09)

POS별 양식표를 두지 않는다. PARSE_SALES 작업이 아래 순서로 처리한다.

1. 파일을 연다. xlsx는 스트리밍 reader, xls는 전용 reader, csv는 UTF-8(BOM 포함) → CP949 순으로 해독한다. 수식은 실행하지 않고 저장된 값만 읽는다.
2. 첫 줄(열 이름)과 그 아래 20줄을 Claude API에 보내 아래 스키마로 답을 받는다. 파일 전체를 보내지 않는다.
   - `date`, `menuName`, `quantity`: 해당하는 열 이름. 셋 다 필수.
   - `amount`: 그 줄의 판매 금액(수량×단가) 열 이름. 없으면 null.
   - `posSystem`: 열 구성으로 보아 `TOSS_POS | OKPOS | BAEMIN_POS` 중 하나가 분명하면 그 값, 아니면 `OTHER`.
3. 받은 열 이름이 실제 첫 줄에 있는지 확인한다. 없는 이름이 오면 `InvalidPayload`다.
4. 전체 행을 코드로 읽는다.
   - 날짜: 날짜 셀 또는 `YYYY-MM-DD`, `YYYY/MM/DD`, `YYYY.MM.DD`, 시각이 붙은 값은 KST 날짜. 날짜로 읽히지 않는 행(합계·소계·빈 줄)은 건너뛴다.
   - 메뉴 이름: 앞뒤 공백 제거, NFC.
   - 수량·금액: 숫자. 음수(환불)는 같은 메뉴·날짜 합계에서 뺀다. 합계가 음수면 0으로 둔다.
5. `period`는 읽은 행의 최소~최대 날짜, `recordCount`는 읽은 행 수, `menuCount`는 서로 다른 메뉴 수, `preview`는 읽은 순서로 앞 5행이다.
6. `sales_import.detection`에 열 매핑(금액 열 포함)·menuCount·preview를 저장하고 NEEDS_CONFIRMATION으로 바꾼다. 공개 응답의 `columnMapping`에는 date/menuName/quantity만 내보낸다.

날짜·메뉴·수량 열 중 하나라도 찾지 못했거나 읽은 행이 0이면 작업은 바로 DEAD다.

APPLY_SALES는 저장된 열 매핑으로 4번을 다시 실행해 반영한다.

- 메뉴를 이름으로 upsert한다. 새 메뉴의 단위는 정책 파일 `[menu_unit]`으로 정한다. 기존 메뉴의 단위는 바꾸지 않는다.
- `(메뉴, 날짜)`별 수량 합계로 daily_sales 칸을 교체한다. 첫 파일 기간 안에서 행이 없는 칸은 저장하지 않고 0으로 읽는다(D08). 겹치는 새 파일에서 누락된 기존 판매 칸은 미정(U02)이다.
- 금액 열이 있으면 메뉴별로 가장 최근 날짜의 `금액 합 ÷ 수량 합`(원 단위 반올림)을 `menu.price`에 넣는다. 수량 합이 0이면 건너뛴다. 금액 열이 없으면 기존 가격을 그대로 둔다.

## 영수증 읽기

SCAN_RECEIPTS 작업은 사진 한 장마다 Claude API를 한 번 부른다. 가게의 재료 이름 목록을 함께 보낸다. 스키마:

- `readable`: 영수증으로 읽을 수 있는지.
- `supplierName`, `phone`(없으면 null), `purchasedOn`.
- `lines[]`: `rawProductName`, `quantity`, `unit`(구매 단위), `unitPrice`(없으면 null), `contentQuantity`·`usageUnit`(품명에 포장량이 적혀 있을 때만. "특란 30구" → 30, 개), `match`.
- `match`: `{ingredientName, kind}`. kind는 `exact`(목록의 재료와 같은 것이 분명함), `similar`(같은 재료일 수 있음), `none`.

읽은 뒤 코드로 처리한다.

1. **못 읽은 것 빼기(D23)**: `readable=false`이거나 거래처·날짜가 없거나 날짜가 미래인 사진은 뺀다. 수량이 없거나 0 이하인 줄은 뺀다. 뺀 내용은 `removedDuplicateTexts`에 안내한다([템플릿](../domain/calculations.md)).
2. **거래처 묶기**: 이름(공백 제거, NFC)이 같으면 같은 거래처다. 기존 supplier와 이름이 같으면 그 id, 아니면 `id=null, isNew=true`.
3. **영수증 묶기와 중복 제거(D22)**: `거래처 + 날짜`가 같으면 영수증 1장이다. 중복은 사진별로 판정한다. 읽은 `(품명, 수량, 단가)` 목록을 정렬해 거래처·날짜와 함께 비교한다. 품명은 앞뒤 공백 제거·NFC, 수량은 숫자로 비교하며 null 단가와 같은 줄의 반복 횟수를 보존한다. 같은 스캔이나 이 가게의 CONFIRMED 스캔에서 이미 반영한 동일 사진별 목록이 있으면 한 장만 반영하고 안내한다. 목록이 다르면 같은 날짜 묶음에 합친다. 같은 날 여러 사진 중 한 장만 다시 올려도 비교할 수 있게 사진별 비교값과 반영 여부를 `receipt_scan.result` 내부에 보관한다.
4. **재료 연결**: 위에서부터 먼저 맞는 규칙을 쓴다.
   1. 같은 가게의 확정된 receipt_line 중 `product_name`이 같은 줄이 있으면 그 재료에 연결한다. 한 번 답한 질문을 다시 묻지 않는 근거다.
   2. `match.kind=exact`이면 그 재료에 연결한다.
   3. `match.kind=similar`이면 질문을 만든다(`rawName`, `candidate`). 같은 `rawName`은 질문 1개로 묶는다.
   4. 그 외는 새 재료다(`id=null, isNew=true`, 이름은 `rawProductName`).
5. 결과(거래처 묶음, 영수증, 줄, 질문, 안내, 단가·전화번호·품명 같은 내부 값)를 `receipt_scan.result`에 저장하고 NEEDS_REVIEW로 바꾼다. 질문 id는 여기서 한 번 정하고 바꾸지 않는다. 공개 응답에는 OpenAPI에 있는 필드만 내보낸다.

확정(confirmReceiptScan)에서 하는 일:

- 확정 시 가게 행을 잠근 뒤 최신 CONFIRMED 결과로 같은 비교를 다시 한다. 중복 사진은 거래처·재료·입고 반영에서 빼고 최종 안내를 저장한다. 반영한 사진의 비교값은 입고와 같은 commit에 기록한다.
- 질문 답이 SAME이면 후보 재료에 연결하고, DIFFERENT면 `rawName`으로 새 재료를 만든다.
- 새 재료는 LATER다. `shelf_life_days`는 정책 파일 `[shelf_life_days]`에서 찾고 없으면 null.
- 재료에 구매 단위·환산 비율이 비어 있으면 영수증의 `unit`, `contentQuantity`, `usageUnit`으로 채운다. 이미 값이 있으면 보존한다. 단위명이 같아도 명시된 포장량과 기존 환산량을 비교한다. 서로 다르면 반영 정책은 [U01](../00-scope-and-decisions.md)이다. 영수증의 구매 단위가 재료의 구매 단위와 다르면 그 줄은 빼고 안내한다(단위를 고치는 화면이 없다).
- 거래처 전화번호는 확인된 번호(`phone_confirmed=true`)가 없을 때만 영수증 번호와 그 영수증 날짜(`phone_read_on`)를 넣는다.

## 레시피 초안

GENERATE_RECIPES 작업은 레시피가 없는 메뉴를 Claude API에 보낸다. 입력은 업종, 메뉴 이름과 단위, 이미 있는 재료 이름 목록이다. 메뉴가 많으면 20개씩 나눠 부른다. 스키마:

- `menus[]`: `menuName`, `items[]`(`ingredientName`, `usageUnit`, `usagePerMenu`).
- `ingredients[]`: `name`, `purchaseUnit`, `contentQuantity`(구매 단위 1개에 든 사용 단위 수량의 추정값. 모르면 null).

검증: `menuName`은 보낸 메뉴 중 하나여야 한다. `usagePerMenu≥0`, `contentQuantity>0`. 같은 재료가 메뉴마다 다른 사용 단위로 오면 처음 나온 단위를 쓰고, `kg↔g`, `L↔ml`만 환산한다. 그 밖의 단위가 섞인 항목은 버린다. 가격, 거래처, 유통기한, 배지는 AI 출력에서 받지 않는다.

저장: 재료를 이름으로 upsert한다(새 재료는 LATER). `shelf_life_days`와 `tags`는 정책 파일로 정한다(`[shelf_life_days]`, `[tags]`). 추정한 구매 단위·환산 비율은 재료에 값이 비어 있을 때만 넣는다. 영수증을 찍기 전 재료 탭의 "1판(30개)"가 이 값이다. 이미 레시피가 있는 메뉴는 건드리지 않는다.

## 주소 → 좌표

GEOCODE_STORE 작업이 카카오 로컬 API 주소 검색에 `road_address`를 보내 첫 결과의 위도·경도를 `store.latitude/longitude`에 저장한다. 결과가 0건이면 좌표를 null로 두고 성공으로 끝낸다. 이 경우 날씨·행사 타일이 나오지 않고 계수는 1.0이다.

## 날씨

REFRESH_CONTEXT 작업이 가게 좌표를 기상청 격자로 바꿔(활용가이드의 변환 식을 그대로 구현하고 가이드의 예시 좌표로 테스트한다) 대상일 예보를 받는다. weather 행에 저장하는 값:

- `temperature_c`: 대상일 영업시간 중 낮 최고기온. 값이 없으면 영업시간 예보 기온의 최댓값.
- `condition`: 영업시간 중 강수 형태가 있으면 그것(비 RAIN, 눈 SNOW, 비/눈 RAIN_SNOW, 소나기 SHOWER), 없으면 하늘 상태(맑음 CLEAR, 구름많음 PARTLY_CLOUDY, 흐림 CLOUDY). 여러 강수 형태가 섞이면 가장 긴 것.
- `summary_text`, `change_text`: [템플릿](../domain/calculations.md).

## 행사

REFRESH_CONTEXT 작업이 서울시 문화행사 정보에서 대상일에 열리는 행사를 받아 event에 upsert한다(id는 그 API의 행사 식별값). 좌표가 없는 행사는 저장하지 않는다. 이 API의 위도·경도 필드는 이름과 값이 뒤바뀌어 있다는 자료가 있으므로, 실제 응답 값이 서울 범위(위도 37.4~37.7, 경도 126.7~127.2)에 맞는지 보고 매핑한다.

가게별 조회는 `대상일의 [00:00, 24:00)`과 `[start_at, end_at)`이 겹치고 Haversine 거리(지구 반지름 6,371,000m, m 단위 반올림)가 `policy.forecast.event_radius_meters` 이하인 행사다. 정렬은 거리, 시작 시각, id 순이다. 시작·끝 시각이 날짜만 있으면 그날 00:00~24:00으로 본다.

**혼잡도(MORNING slot에서만)**: 서울 실시간 도시데이터는 주요 121개 장소의 현재 혼잡도와 12시간 예측 인구를 준다. 전날 밤에는 다음 날 낮 값이 없으므로 아침 갱신에서만 채운다.

1. 서울시가 배포하는 121개 장소 목록(장소 이름·코드·좌표)을 저장소에 파일로 둔다.
2. 가게 반경 안 행사마다 가장 가까운 장소를 찾는다. 500m 안에 장소가 없으면 그 행사의 혼잡도 값은 null로 둔다.
3. 그 장소의 예측 인구에서 가장 큰 시간대를 `peak_from~peak_to`, 그때의 예측 인구를 `expected_visitors`, 그 시간대의 혼잡도 단계를 `crowd_level`에 넣는다. 단계는 4단계를 3개로 줄인다: 여유·보통 → NORMAL, 약간 붐빔 → BUSY, 붐빔 → VERY_BUSY.

혼잡도는 화면 표시에만 쓴다. 준비량 계수는 행사 유무로만 정한다(C02).

## 푸시

프론트(PWA)는 브라우저의 푸시 구독 객체를 `JSON.stringify`한 문자열을 `putPushToken`의 `token`으로 보낸다. 서버는 그 문자열을 `store.push_token`에 그대로 저장한다. 프론트가 구독할 때 쓰는 VAPID 공개키는 프론트 빌드 설정으로 전달한다(이 API에 공개키 엔드포인트는 없다).

SEND_PUSH 작업은 `pywebpush`로 `{title, body, url:"/"}`를 보낸다. 문구는 [템플릿](../domain/calculations.md)을 따른다. 푸시 서비스가 404나 410을 돌려주면 구독이 끝난 것이므로, `store.push_token`이 보낼 때 쓴 값과 같을 때만 null로 비운다. 아이폰은 홈 화면에 추가한 PWA에서만 푸시가 온다. SMS·카카오톡을 서버가 보내는 기능은 없다.
