# 공급자 응답 fixture

| 파일 | 출처 |
|---|---|
| `seoul_cultural_events.json` | 2026-11-20 서울 열린데이터광장 `culturalEventInfo`를 공개 샘플 키(`sample`)로 호출한 실제 응답 |
| `seoul_cultural_events_by_date.json` | 같은 API에 날짜(2026-11-23)를 넣어 호출한 실제 응답. 그 날짜에 시작하거나 끝나는 행사만 온다 |
| `seoul_no_data.json` | 같은 API에서 해당 데이터가 없을 때의 실제 응답 |
| `seoul_citydata_population.json` | 2026-11-20 `citydata`를 샘플 키로 호출한 실제 응답에서 인구 항목(`LIVE_PPLTN_STTS`)만 남긴 것 |
| `kma_vilage_fcst_guide_sample.xml` | 기상청 단기예보 조회서비스 오픈API 활용가이드(2610)의 응답 예제. 실제 호출 응답이 아니다 |
| `kakao_address_documented_fields.json` | 카카오 로컬 API 문서의 '주소로 좌표 변환' 예제에서 문서에 적힌 필드만 옮긴 것. 실제 호출 응답이 아니다 |
| `kakao_address_live.json` | 2026-11-20 카카오 로컬 API 주소 검색을 실제 키로 호출한 응답 |
| `kma_vilage_fcst_live.xml` | 2026-11-20 기상청 단기예보 조회서비스를 실제 키로 호출한 응답의 첫 30건 |

Claude API와 웹 푸시는 실제 호출 응답 fixture가 없다.
