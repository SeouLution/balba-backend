# recordSent

`POST /stores/{storeId}/purchase-orders/{purchaseOrderId}/send-records`

3.1 문자 · 카카오톡 · 복사 (보낸 방법 기록)

## 공개 계약

- operationId: `recordSent`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수
- `purchaseOrderId` (path): {type: string} — 필수

요청 본문: `application/json`, 필수, 인라인 타입 (아래).

```yaml
type: object
required:
- channel
properties:
  channel:
    $ref: '#/components/schemas/SendChannel'
  recipientPhone:
    type: string
```

| 성공 상태 | 응답 |
|---|---|
| 204 | 본문 없음 |

## 책임과 경계

ordering / `RecordSent.execute(StoreContext, purchaseOrderId, channel:SMS|KAKAOTALK|COPY, recipientPhone:string?) → void`. 기기의 공유/전송 행동 기록만 저장한다. 서버 SMS·카카오톡 발송 없음.

## 처리 순서

1. store→purchase_order 잠금, supplier를 통해 소속 검증.
2. recipientPhone이 있으면 공백/하이픈/괄호를 제거하고 선행 +를 보존한다. DESIGN: 나머지 숫자 7~15자리, 공백만인 입력은 422. 국내 번호를 자동 국제번호로 추측 변환하지 않는다.
3. 입력 번호가 있으면 `SupplierWriter.confirmPhone`으로 supplier.phone에 저장하고 phone_confirmed=true, phone_read_on=null로 전환한다. 이전 확인 번호와 달라도 사용자의 명시 입력으로 갱신한다. 생략이면 기존 번호/확인 상태 보존.
4. 동일 channel+입력 번호의 유효 결과가 이미 기록되어 있으면 기존 sent_at 유지. 그 외 channel과 Clock 시점 sent_at 저장. channel이 같고 새 번호가 기존 confirmed 번호와 같으면 동일 재요청으로 본다.
5. supplier 갱신과 발주 기록을 함께 commit, 204.

## 원자성·실패

없음/타 store 404, 번호/channel 오류 422. COPY는 전화번호 없이 가능하며 다른 channel도 계약상 번호 필수가 아니므로 생략 허용. sent_at은 기기가 요청을 보낸 시각이지 실제 수신 증명은 아니다. receipt_line/stock은 절대로 바뀌지 않는다.

## 완료 조건

모든 channel, 번호 생략/첫 확인/정정, 반복 sent_at 보존, 다른 channel 최신값, 실제 provider 호출 0회, 재고 불변을 검증한다.
