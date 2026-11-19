# 저장 구조와 마이그레이션

## 기반

PostgreSQL, SQLAlchemy 2.x async, Alembic을 쓴다. 버전은 구현 시 Python 3.12/ARM64에서 확인해 `requirements.txt`에 고정한다. SQLite로 대신한 테스트는 PostgreSQL의 잠금·unique·JSON 동작을 검증한 것으로 보지 않는다.

**저장 구조의 기준은 [erd.dbml](../../erd.dbml) 하나다.** 이 스펙은 ERD에 없는 테이블·컬럼을 쓰지 않는다. 구현 중 더 필요해지면 ERD를 먼저 고치고 [결정 원장](../00-scope-and-decisions.md)에 적는다.

ERD의 text ID, composite PK, 날짜/timestamptz, nullable을 그대로 따른다. json은 JSONB, 수량은 numeric(18,6), 금액은 bigint로 만든다. enum note는 CHECK 제약으로 만든다. 좌표 범위, 0 이상 수량·가격, `content_quantity>0`, `shelf_life_days>0`에도 CHECK를 둔다.

## 테이블별 소유와 불변식

| 테이블 | 쓰는 모듈 | 지킬 것 |
|---|---|---|
| store | stores | 요일 7개, 좌표는 둘 다 null이거나 둘 다 값. `first_forecast_at`은 첫 예측 때 한 번만 채움 |
| menu | catalog | `(store_id,name)` unique. 판매가를 모르면 null. 판매가는 가장 최근에 반영한 파일의 값 |
| ingredient | catalog | CORE/LATER. `usage_unit`, `purchase_unit`, `content_quantity`가 모두 있어야 환산 가능 |
| recipe_item | catalog | 같은 가게의 메뉴·재료만 |
| sales_import | sales | `file_path`는 내부 저장 위치. `detection`에 열 매핑(금액 열 포함)과 미리보기 |
| daily_sales | sales | `(menu_id,sold_on)` 칸 교체. 더하지 않음 |
| supplier | suppliers | `(store_id,name)` unique. 확인된 전화·요일은 영수증이 덮지 않음 |
| receipt_scan | receipts | `images`에 사진 위치 배열. `result`에 사진별 비교값·반영 여부, 검토 결과·질문·확정 답변과 최종 안내 |
| receipt_line | receipts | 확정한 scan에서만 생김. 영수증 1장 = 같은 `supplier_id`·`purchased_on` 묶음(ERD) |
| forecast | forecasting | 작업은 `adjusted`·`produced`를 쓰지 않음 |
| weather | forecasting | `(store_id,target_date)` 한 행을 덮어씀 |
| event | forecasting | 가게와 무관한 공통 자료. 반경·기간으로 조회 |
| purchase_order | ordering | `message_text`, `total_amount`는 생성 시점 값으로 고정 |
| purchase_order_line | ordering | 구매 단위 수량. 입고로 세지 않음 |
| discount | discounts | `original_price`, `waste_cost_avoided`, `revenue_reduction`, `promo_text`는 생성 시점 값 |
| background_job | infrastructure/jobs | `dedupe_key` unique. slot 예측 결과는 [작업 payload 계약](../02-boundary-contracts.md)대로 같은 행에 보관 |

2026-11-19에 ERD에 추가한 것은 `background_job` 테이블, `store.first_forecast_at`, `receipt_scan.images` 세 가지다. 응답에 필요하지만 저장하지 않는 값은 조회할 때 계산한다: 할인가(`original_price`와 할인 값으로 계산), 거래처 영수증 수(`receipt_line`의 서로 다른 `purchased_on` 수), 발주·재고·알림·할인 추천.

## 인덱스

- ERD의 unique와 PK가 대부분의 조회를 덮는다. 같은 컬럼으로 시작하는 인덱스를 중복해 만들지 않는다.
- 추가: `receipt_line(ingredient_id, purchased_on DESC, id DESC)`, `daily_sales(sold_on, menu_id)`, `forecast(target_date, menu_id)`, `purchase_order(supplier_id, created_at DESC)`, `discount(menu_id, created_at DESC)`, `background_job(state, available_at)`.

## 제약

- FK에 ON DELETE CASCADE를 걸지 않는다. 삭제 API가 없다.
- store 소속은 application에서 검증하고 SQL에도 `WHERE store_id`를 건다. recipe_item, receipt_line, purchase_order_line이 다른 가게의 행을 가리키지 못하게 쓰기 유스케이스가 저장 전에 확인한다.
- 쓰기는 store 행을 `FOR UPDATE`로 잠그고 시작한다([아키텍처](../01-architecture.md)).

## 마이그레이션

1. ERD 16개 테이블과 PK/FK/CHECK/인덱스를 Alembic 첫 revision으로 만든다.
2. 서버 시작 전에 `alembic upgrade head`를 한 번 실행한다. 애플리케이션 startup의 `create_all`로 대신하지 않는다.
3. 판매·입고 같은 업무 데이터를 seed로 넣지 않는다.
