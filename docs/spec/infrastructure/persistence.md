# 저장 구조와 마이그레이션

## 기반

PostgreSQL, SQLAlchemy 2.x async, Alembic을 쓴다. 버전은 구현 시 Python 3.12/ARM64에서 확인해 `requirements.txt`에 고정한다. SQLite로 대신한 테스트는 PostgreSQL의 잠금·unique·JSON 동작을 검증한 것으로 보지 않는다.

**저장 구조의 기준은 [erd.dbml](../../erd.dbml) 하나다.** 이 스펙은 ERD에 없는 테이블·컬럼을 쓰지 않는다. 구현 중 더 필요해지면 ERD를 먼저 고치고 [결정 원장](../00-scope-and-decisions.md)에 적는다.

ERD의 text ID, composite PK, 날짜/timestamptz, nullable을 그대로 따른다. json은 JSONB, 수량과 단가(`receipt_line.unit_price`)는 numeric(18,6), 금액은 bigint로 만든다. enum note는 CHECK 제약으로 만든다. `ingredient.unit`은 `개`·`g`만 받는다. 좌표 범위, 0 이상 수량·단가에도 CHECK를 둔다.

## 테이블별 소유와 불변식

| 테이블 | 쓰는 모듈 | 지킬 것 |
|---|---|---|
| store | stores (`forecast_model` 칸만 forecasting) | 요일 7개, 좌표는 둘 다 null이거나 둘 다 값. `first_forecast_at`은 첫 예측 때 한 번만 채움. `forecast_model`은 FIT_FORECAST_MODEL만 통째로 덮어씀 |
| menu | catalog | `(store_id,name)` unique. 단위 컬럼이 없다. 메뉴 수량은 모두 인분(D46) |
| ingredient | catalog | CORE/LATER. `unit`은 `개` 또는 `g`이고 만든 뒤 바꾸지 않는다(D47) |
| recipe_item | catalog | 같은 가게의 메뉴·재료만 |
| sales_import | sales | `file_path`는 내부 저장 위치. `detection`에 열 매핑과 미리보기 |
| daily_sales | sales | `(menu_id,sold_on)` 칸 교체. 더하지 않음 |
| supplier | suppliers | `(store_id,name)` unique. 확인된 전화·요일은 영수증이 덮지 않음 |
| receipt_scan | receipts | `images`에 사진 위치 배열. `result`에 사진별 비교값·반영 여부, 검토 결과·질문·확정 답변과 최종 안내 |
| receipt_line | receipts | 확정한 scan에서만 생김. 영수증 1장 = 같은 `supplier_id`·`purchased_on` 묶음(ERD). `quantity`는 재료 단위 입고량, `unit_price`는 재료 단위 1개당 원 |
| forecast | forecasting | 작업은 `adjusted`·`produced`를 쓰지 않음. `produced`는 화면 표시용(D43) |
| weather | forecasting | `(store_id,target_date)` 한 행을 덮어씀. 대상일 예보 |
| weather_observation | forecasting | 가게와 무관한 공통 자료. `observed_on` 한 행을 덮어씀. 과거 관측값이며 예측 모델 계산에만 씀 |
| event | forecasting | 가게와 무관한 공통 자료. 반경·기간으로 조회 |
| purchase_order | ordering | `message_text`, `total_amount`는 생성 시점 값으로 고정 |
| purchase_order_line | ordering | 재료 단위 수량. 입고로 세지 않음 |
| background_job | infrastructure/jobs | `dedupe_key` unique. slot 예측 결과는 [작업 payload 계약](../02-boundary-contracts.md)대로 같은 행에 보관 |

2026-11-19에 ERD에 추가한 것은 `background_job` 테이블, `store.first_forecast_at`, `receipt_scan.images` 세 가지다. 2026-11-21에 `weather_observation` 테이블과 `store.forecast_model`을 추가하고 `discount` 테이블과 `menu.price`를 삭제했다(D39, D40, D42). 같은 날 `menu.unit`, `ingredient.purchase_unit`, `ingredient.content_quantity`를 삭제하고 `ingredient.usage_unit`을 `unit`으로 바꿨으며 `receipt_line.unit_price`를 numeric으로 바꿨다(D46, D47, D49). 응답에 필요하지만 저장하지 않는 값은 조회할 때 계산한다: 거래처 영수증 수(`receipt_line`의 서로 다른 `purchased_on` 수), 발주·재고·재고 부족 목록.

## 인덱스

- ERD의 unique와 PK가 대부분의 조회를 덮는다. 같은 컬럼으로 시작하는 인덱스를 중복해 만들지 않는다.
- 추가: `receipt_line(ingredient_id, purchased_on DESC, id DESC)`, `daily_sales(sold_on, menu_id)`, `forecast(target_date, menu_id)`, `purchase_order(supplier_id, created_at DESC)`, `background_job(state, available_at)`.

## 제약

- FK에 ON DELETE CASCADE를 걸지 않는다. 삭제 API가 없다.
- store 소속은 application에서 검증하고 SQL에도 `WHERE store_id`를 건다. recipe_item, receipt_line, purchase_order_line이 다른 가게의 행을 가리키지 못하게 쓰기 유스케이스가 저장 전에 확인한다.
- 쓰기는 store 행을 `FOR UPDATE`로 잠그고 시작한다([아키텍처](../01-architecture.md)).

## 마이그레이션

1. ERD의 테이블과 PK/FK/CHECK/인덱스를 Alembic revision으로 만든다. 2026-11-21 변경(`weather_observation` 추가, `store.forecast_model` 추가, `discount` 삭제, `menu.price`·`menu.unit` 삭제, 재료 단위 정리)은 새 revision으로 만든다. 기존 가게의 `forecast_model`은 null로 두며 다음 판매 반영 때 채워진다. 기존 재료 데이터는 revision에서 옮긴다: 사용 단위 `장` 등 낱개 단위는 `개`, `ml`은 `g`으로 바꾸고, 구매 단위 수로 저장된 입고량·발주 수량에는 `content_quantity`를 곱하고 단가는 `content_quantity`로 나눈다. 사용 단위나 환산량이 없어 옮길 수 없는 재료가 실제 데이터에 있으면 임의로 지우거나 채우지 않고 사용자에게 확인한다.
2. 서버 시작 전에 `alembic upgrade head`를 한 번 실행한다. 애플리케이션 startup의 `create_all`로 대신하지 않는다.
3. 판매·입고 같은 업무 데이터를 seed로 넣지 않는다.
