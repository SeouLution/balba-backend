# HTTP 공통 계약

## 경로와 DTO

25개 operation은 `/v1` prefix 아래에 등록한다. 엔드포인트 문서에는 OpenAPI처럼 prefix를 뺀 경로를 쓴다. 기존 루트 `/health`, `/openapi.yaml`, `/scalar`는 그대로 유지한다. operationId를 명시하고 정적 OpenAPI와 런타임 라우트를 계약 테스트로 대조한다.

request/response 클래스는 별도다. 내부 snake_case와 공개 camelCase는 alias로 연결하고, 응답에 ORM 필드나 내부 diagnostics가 섞이지 않게 명시적 presenter를 둔다. 전체 필드·필수/nullable·enum의 기준은 [openapi.yaml](../../openapi.yaml) 하나다. 이 스펙은 스키마를 다시 옮겨 적지 않는다.

파일의 `contentMediaType`는 OpenAPI 3.1 원본 표현이다. 실제 FastAPI 구현은 UploadFile/복수 UploadFile로 multipart를 받고, 문자열 JSON DTO로 잘못 생성하지 않는다. 자동 생성 runtime schema가 원본 표현과 다르면 static spec을 바꾸기 전에 transport 계약 테스트로 업로드가 동작하는지 확인한다.

선택 필드는 다음 원칙을 지킨다.

- `required`이면서 nullable이면 반드시 키를 쓰고 값이 없으면 null이다. 예: PriceInfo.unitPrice.
- 선택이면서 non-nullable이면 모를 때 생략한다. 예: IngredientSummary.usageUnit, Discount.wasteCostAvoided.
- 선택 nullable은 아래 모듈 규칙에 따라 null을 명시한다. IngredientSummary.purchaseUnit/price/supplier, ForecastItem.previousRecommended/produced가 해당한다.
- 목록은 빈 배열이며 null이 아니다. `NEEDS_CONFIRMATION`의 detection, `NEEDS_REVIEW`의 groups는 조건부 필수다.
- 204와 본문 없는 202는 진짜 빈 body다. `null`이나 `{}`를 반환하지 않는다.

## 검증 단계

1. transport: JSON/멀티파트/날짜/시각/enum/수치형/required 검사.
2. structural: 항목 중복, 요일 7개, 크기, batch empty 규칙 검사. 수량은 소수 6자리로 반올림해 받는다(D26). NaN·Infinity·음수는 422.
3. ownership: store 실존 후 모든 하위 ID가 store에 속하는지 검사. 존재하지 않거나 타 store이면 동일하게 404.
4. state/domain: 현재 상태에서 허용되는 전이, 환산 비율, 가격, 날짜 제약 검사. 실패는 422.

알 수 없는 추가 JSON 키는 원본이 `additionalProperties:false`가 아니므로 거부하지 않고 무시한다. 문자열 숫자·bool을 number로 조용히 받지 않는 엄격한 타입 검증을 기본으로 한다. 문자열 정규화는 저장용 이름의 앞뒤 공백 제거/NFC만 수행하며 이름 유사도를 근거로 다른 품목을 자동 병합하지 않는다.

## 실패 변환

| 실패 | 상태 | 외부 body |
|---|---|---|
| malformed body, 잘못된 날짜·enum, 누락, 파일 한도, 불가능한 전이 | 422 | `{"code":"INVALID_REQUEST"}` |
| store/하위 리소스 없음 또는 다른 store 소속 | 404 | `{"code":"NOT_FOUND"}` |
| DB/외부 처리 실패, 재시도가 끝난 작업(DEAD), 예상하지 못한 오류 | 500 | `{"code":"INTERNAL_ERROR"}` |

FastAPI request validation, HTTP exception 및 예기치 않은 예외의 전역 handler가 필요하다. request body의 `detail` 배열이나 provider 오류를 그대로 내보내지 않는다. 응답 검증 실패는 서버 오류 500이다. 지원하지 않는 method는 405를 유지하되 body는 `{code:INVALID_REQUEST}`로 통일하고 Allow 헤더를 유지한다. 이는 업무 실패의 422와 다른 HTTP transport 상황이다.

request ID는 헤더/로그에만 쓰고 Error body에 추가하지 않는다. API 단계에서 제한 초과 업로드는 422로 정규화한다. 프록시가 그 전에 거절한 413은 앱 계약 밖이므로 프록시 한도를 앱보다 크게 설정한다.

## 날짜·시간·수량

Clock.now()는 요청 시작 시 1회 읽어 StoreContext에 고정한다. 환경변수 `DEMO_NOW`가 있으면 Clock은 항상 그 시각을 돌려준다(D27). 날짜/요일 계산은 `Asia/Seoul`, DB 시점은 UTC. `THIS_WEEK`는 KST 월요일 00:00부터 현재 요청시각까지다. 자정 경계 테스트는 UTC 서버 timezone과 무관해야 한다.

수량은 [공통 타입](02-boundary-contracts.md)의 Decimal 규칙을 따른다. response number를 문자열로 만들지 않는다. UI의 `+1`은 프론트가 합산 후 절대 수량을 보낸다. API가 한 번 더 증가시키지 않는다.

## 재요청과 동시성

- PUT는 동일 입력 반복 시 같은 업무 결과. 동시에 다른 값은 store 잠금을 나중에 얻은 요청의 값이 남는다.
- import confirm은 APPLYING/COMPLETED에서 202 빈 body 재반환. scan confirm은 같은 결정 집합으로 확정된 경우 204, 다른 결정으로 재확정하면 422.
- purchase order/discount POST에는 공개 idempotency key가 없다. 같은 POST가 다시 오면 새 리소스를 생성한다. 이를 멱등이라고 주장하지 않으며 UI는 중복 클릭을 막아야 한다. 후속 키 추가는 별도 API 변경이다.
- send-records는 최신 전송 방식의 기록이다. 동일 channel/정규화 번호 재요청은 기존 sent_at을 보존하고, 다른 channel 기록은 최신으로 갱신한다. 모든 전송 이벤트 이력을 제공하는 API가 아니다.
- 작업이 다시 실행돼도 업무 반영은 한 번이다. 각 handler는 업무 상태(import/scan 상태)를 먼저 확인하고 이미 반영됐으면 아무것도 하지 않는다.

## 읽기 응답과 오류 우선순위

리소스 소속을 먼저 확인한 뒤 상태/값을 검증한다. 타 가게 리소스의 내부 상태를 422로 노출하지 않는다. GET는 DB에 쓰지 않고 외부 API를 호출하지 않는다. 발주 계획·재고·알림·할인 추천은 GET 시점에 저장된 데이터로 계산해 돌려준다. 데이터가 없으면 스키마가 허용하는 빈 목록/누락/null을 반환한다.
