# putPushToken

`PUT /stores/{storeId}/push-token`

푸시 토큰 등록 (전날 밤 20:00 · 아침 07:30 알림)

## 공개 계약

- operationId: `putPushToken`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수

요청 본문: `application/json`, 필수, 인라인 타입 (아래).

```yaml
type: object
required:
- token
properties:
  token:
    type: string
```

| 성공 상태 | 응답 |
|---|---|
| 204 | 본문 없음 |

## 책임과 경계

stores / `RegisterPushToken.execute(StoreContext, token:string) → void`. 프론트는 웹 PWA이고 `token`에는 브라우저 푸시 구독 객체를 `JSON.stringify`한 문자열이 온다(D24).

## 처리 순서

1. token이 JSON으로 읽히고 `endpoint`(https URL)와 `keys.p256dh`, `keys.auth`가 있는지 확인한다. 아니면 422. 길이는 최대 4096자.
2. store를 잠그고 `store.push_token`을 받은 문자열 그대로 교체한다. 같은 값이면 바꾸지 않는다. 가게당 구독은 하나다. 다른 기기에서 다시 등록하면 그 기기로 바뀐다.
3. commit 후 204 빈 body. 이 요청으로 시험 알림을 보내지 않는다.

## 원자성·실패

없는 store는 404, 형식이 틀린 token은 422, DB 실패는 500. 동시에 들어오면 나중에 잠금을 얻은 값이 남는다. 푸시를 보내다 구독 만료(404/410)를 받으면, 그때 DB 값이 보낼 때 쓴 값과 같을 때만 null로 비운다([푸시](../infrastructure/integrations.md)).

## 완료 조건

같은 값 반복, 교체, 형식 오류, 전송 중 token이 바뀐 경우, 204 빈 body, token이 로그에 남지 않는 것을 검증한다. 삭제와 여러 기기 동시 구독은 계약 밖이다.
