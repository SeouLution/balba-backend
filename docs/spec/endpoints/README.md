# 엔드포인트 구현 인덱스

OpenAPI의 25개 operation과 1:1 대응한다. 각 파일은 공개 계약, 모듈 경계, 처리 순서, 원자성/실패, 완료 조건을 정의한다. `/health`, `/openapi.yaml`, `/scalar`는 별도의 [기반 유지 조건](../infrastructure/runtime.md)이다.

| operationId / 상세 명세 | Method | 경로 | 화면 / 목적 |
|---|---|---|---|
| [createStore](createStore.md) | POST | `/stores` | 1.1 가게 정보 저장 |
| [putPushToken](putPushToken.md) | PUT | `/stores/{storeId}/push-token` | 푸시 토큰 등록 (전날 밤 20:00 · 아침 07:30 알림) |
| [listIngredientCandidates](listIngredientCandidates.md) | GET | `/stores/{storeId}/ingredient-candidates` | 1.4 핵심 재료 고르기 |
| [setCoreIngredients](setCoreIngredients.md) | PUT | `/stores/{storeId}/core-ingredients` | 1.4 핵심 재료 확정 ('N개로 시작하기') |
| [listRecipes](listRecipes.md) | GET | `/stores/{storeId}/recipes` | 1.6 레시피 초안 |
| [saveRecipes](saveRecipes.md) | PUT | `/stores/{storeId}/recipes` | 1.6 '저장하고 홈으로' |
| [createSalesImport](createSalesImport.md) | POST | `/stores/{storeId}/sales-imports` | 1.2 이 기기에서 엑셀 올리기 |
| [getSalesImport](getSalesImport.md) | GET | `/stores/{storeId}/sales-imports/{importId}` | 1.3 열 확인 (진행 상태 조회) |
| [confirmSalesImport](confirmSalesImport.md) | POST | `/stores/{storeId}/sales-imports/{importId}/confirm` | 1.3 열 확인 |
| [getHome](getHome.md) | GET | `/stores/{storeId}/home` | 2.0-1 온보딩 직후 · 2.2 당일 아침 · 2.7-1 행사 펼친 상태 · 2.9 행사 당일 아침 |
| [adjustForecast](adjustForecast.md) | PUT | `/stores/{storeId}/forecasts/{date}/menus/{menuId}` | 2.0-1 · 2.7-1 준비량 +/− |
| [setProduction](setProduction.md) | PUT | `/stores/{storeId}/production/{date}/menus/{menuId}` | 2.2 '만듦 +1' · 2.3 '기록하기' |
| [getOrderPlan](getOrderPlan.md) | GET | `/stores/{storeId}/order-plans/{targetDate}` | 2.8 행사 반영 발주 · 3.8 상단 '내일 발주' |
| [listPurchaseOrders](listPurchaseOrders.md) | GET | `/stores/{storeId}/purchase-orders` | 3.8 지난 발주서 |
| [createPurchaseOrders](createPurchaseOrders.md) | POST | `/stores/{storeId}/purchase-orders` | 2.8 '발주서 만들기' |
| [recordSent](recordSent.md) | POST | `/stores/{storeId}/purchase-orders/{purchaseOrderId}/send-records` | 3.1 문자 · 카카오톡 · 복사 (보낸 방법 기록) |
| [listIngredients](listIngredients.md) | GET | `/stores/{storeId}/ingredients` | 3.2 재료 탭 |
| [listSuppliers](listSuppliers.md) | GET | `/stores/{storeId}/suppliers` | 3.7 거래처 주문 요일 |
| [saveOrderDays](saveOrderDays.md) | PUT | `/stores/{storeId}/suppliers/order-days` | 3.7 '저장' |
| [createReceiptScan](createReceiptScan.md) | POST | `/stores/{storeId}/receipt-scans` | 1.5 · 3.5 '다 찍었어요' (여러 장 한 번에) |
| [getReceiptScan](getReceiptScan.md) | GET | `/stores/{storeId}/receipt-scans/{scanId}` | 3.6 거래처별 자동 분류 |
| [confirmReceiptScan](confirmReceiptScan.md) | POST | `/stores/{storeId}/receipt-scans/{scanId}/confirm` | 3.6 '저장' |
| [getDiscountRecommendations](getDiscountRecommendations.md) | GET | `/stores/{storeId}/discount-recommendations` | 4.0 할인 탭 상단 카드 · 4.0-2 할인 추천 목록 |
| [listDiscounts](listDiscounts.md) | GET | `/stores/{storeId}/discounts` | 4.0 할인 기록 (오늘 · 이번 주 · 전체) |
| [createDiscount](createDiscount.md) | POST | `/stores/{storeId}/discounts` | 4.1 '문구 만들기' (dryRun=true면 기대 절감만 계산) |

## 공통 선행 문서

- [설계 결정](../00-scope-and-decisions.md), [아키텍처](../01-architecture.md), [경계 타입](../02-boundary-contracts.md), [HTTP 규칙](../03-http-contract.md).
- [저장 구조](../infrastructure/persistence.md), [비동기 작업](../infrastructure/jobs.md), [계산 규칙](../domain/calculations.md).
- [검증 기준](../validation.md), [구현 순서·프롬프트](../implementation-plan.md).
