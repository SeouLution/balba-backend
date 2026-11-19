# SeouLution 백엔드 구현 명세

이 문서 묶음은 **OpenAPI 0.4.0-draft의 25개 operation을 구현하기 위한 설계 기준**이다. 실행 코드가 아니라 구현 작업의 입력이다.

기준 자료는 두 개다. 공개 API는 [openapi.yaml](../openapi.yaml), 저장 구조는 [erd.dbml](erd.dbml). 이 명세는 두 파일에 없는 필드·테이블을 쓰지 않으며 스키마를 다시 옮겨 적지 않는다.

## 읽는 순서

1. [출처·범위·설계 결정](spec/00-scope-and-decisions.md): 결정 원장과 Figma 대조 결과.
2. [전체 아키텍처](spec/01-architecture.md): 실행 구조, 모듈 소유권, 의존 방향.
3. [공통 타입·포트](spec/02-boundary-contracts.md), [HTTP 규칙](spec/03-http-contract.md).
4. [정책 기본값](spec/policy-defaults.toml): 계수, 유통기한, 메뉴 단위, 할인 기본값. AI 초안이며 이 파일만 고치면 된다.
5. [계산 규칙·문장 템플릿](spec/domain/calculations.md).
6. [DB](spec/infrastructure/persistence.md), [비동기 작업](spec/infrastructure/jobs.md), [외부 연동](spec/infrastructure/integrations.md), [실행·운영](spec/infrastructure/runtime.md).
7. [엔드포인트별 명세](spec/endpoints/README.md).
8. [검증 시나리오](spec/validation.md), [구현 순서와 프롬프트](spec/implementation-plan.md).

## 구현자에게 주는 계약

- 공개 DTO의 이름·필드·필수 여부·nullable·상태 코드는 OpenAPI를 따른다. 내부 타입은 공개 DTO와 분리한다.
- 결정 원장의 `USER`는 사용자가 정한 것, `DESIGN`은 이 문서가 고른 기본값이다. 둘 다 구현 기준이다.
- 다른 모듈의 ORM 객체·세션·임의 JSON을 넘기지 않는다. 모듈 경계에는 명명된 불변 타입과 포트만 쓴다.
- 발주 계획, 재고, 알림은 각각 한 곳에서만 계산한다([아키텍처](spec/01-architecture.md)).
- 각 엔드포인트 문서의 검증, 처리 순서, 원자성, 재요청, 실패, 완료 조건을 함께 구현한다. 성공 경로만 만드는 것은 완료가 아니다.
- 모르는 재고·가격·유통기한을 0이나 임의 숫자로 채우지 않는다.
- 구현을 시작할 때 원본 해시를 확인한다. 달라졌으면 영향받는 문서를 먼저 고친다.

## 현재 상태

2026-11-19 기준 저장소에는 FastAPI 실행 기반과 `/health`, `/openapi.yaml`, `/scalar`만 구현되어 있다. PDF, OpenAPI, ERD, Figma(TASK 1~4 화면)를 모두 확인했다.

이 명세로 만드는 것은 시연용 MVP다. 사용자 인증, 판매의 지속 수집, 재고 실사, 실패 재시도 API는 원본에 없으므로 범위 밖이다.
