# 감정 기반 메모리 시스템 (Emotion-Weighted Memory System)

## 프로젝트 기획서 v0.4

---

## 변경 이력

| 버전 | 날짜 | 주요 변경 |
|------|------|-----------|
| v0.1 | 2026-02-27 | 초안 작성 |
| v0.2 | 2026-02-27 | 다이얼 시스템, 앵커 메모리 추가 |
| v0.3 | 2026-02-27 | 앵커 TTL, 빈도 가중치, 페르소나 다이얼 통합 |
| v0.4 | 2026-03-05 | **중력장 시뮬레이션 결과 반영.** 2계층 메모리 확정, 재태깅 메커니즘 재설계, 핀 시스템 신규, 피드백 기반 최적화 추가, 에코챔버 방지 메커니즘 추가 |
| v0.4.1 | 2026-03-05 | **설계 리뷰 반영.** 시간 감쇠 수식 수정, 검색 스코어 가중치 정규화, 피드백 판정 방법 정의, 콜드 스타트 전략 추가, 핀 TTL UX 개선, 세션 관리 정의 |

---

## 1. 프로젝트 개요

### 배경

현재 주류 LLM 에이전트와 메모리 솔루션(RAG, MemGPT 등)은 텍스트의 '의미적 유사도'에만 의존하여 정보의 우선순위("무엇이 중요한가")를 판단하지 못하는 근본적인 한계가 있다. 본 프로젝트는 인간이 편도체와 해마를 통해 '감정'으로 기억의 중요도를 판별하고 연상하는 생물학적 메커니즘을 엔지니어링 패턴으로 차용하여, LLM의 기억 구조를 혁신하는 것을 목표로 한다.

### 핵심 철학

- **가중치로서의 감정:** 감정은 AI의 단순한 의인화 연기가 아니라, 메모리 관리를 위한 데이터베이스 가중치(Weight)이자 인출(Retrieval)의 기준 축이다.
- **소프트 아카이브 (Soft-Archive):** 기계의 장점인 '무한한 용량'을 살려 데이터의 물리적 삭제(Hard Delete)는 배제하고, 인출 계수 조절을 통해 망각을 통제한다.
- **이벤트 기반 검색:** 기억 간 연상은 상시 작동하는 물리적 힘이 아니라, 검색 시점에만 발생하는 이벤트로 처리한다. (v0.4 시뮬레이션에서 확인: 상시 중력 모델은 N체 문제로 필연적 붕괴)

### 이론적 배경

| 개념 | AI 대응 | 비고 |
|------|---------|------|
| 인간 감정 | 가중치 벡터 | 개체별로 다른 매핑 → 개성 |
| 해마 (기억 저장) | 감정 태깅 + DB 저장 | 중요도 기반 필터링 |
| 편도체 (감정 평가) | 분류 모델 (백맨) | 맥락 기반 감정 분석 |
| 연상 기억 | 이벤트 기반 태그 검색 | 검색 시점에만 활성화 |
| 재고정화 (reconsolidation) | 재태깅 (강도 조절) | recall 시 감정 강도 업데이트 |
| 기억 강화 (rehearsal) | 유저 피드백 가중치 | 사용된 기억은 강화 |
| 수면 시냅스 항상성 | 시간 감쇠 계수 | 전역적 relevance 감소 |
| 전전두엽 능동적 유지 | 핀 메모리 | 명시적 중요 정보 강제 유지 |
| Neurosymbolic AI | 규칙(DB/로직) + 유연성(LLM) | 본 시스템의 구조 자체 |

---

## 2. 메모리 아키텍처: 2계층 구조

### 2.1 워킹 메모리 (Working Memory)

직근 대화 10턴을 원본 그대로 유지하는 단기 기억 공간.

- **용량:** 최근 10턴 (유저 입력 + AI 응답)
- **특성:** 원본 보존, 압축 없음, FIFO 롤링
- **10턴 만료 시:** 백맨이 감정 + 장면 태깅 → 장기기억 DB로 이관
- **핀 슬롯:** 아래 2.3절 참조

### 2.2 장기기억 (Long-term Memory)

감정과 장면으로 태깅된 기억의 영구 저장소. 소프트 아카이브 원칙에 따라 데이터의 물리적 삭제는 없으며, 검색 계수 조절로 망각을 통제한다.

- **저장 단위:** 대화 요약 + 감정 벡터 + 장면 태그 + 메타데이터
- **검색 기준:** 감정 유사도 × 장면 유사도 × 시간 감쇠 × 피드백 가중치
- **인출 시:** 관련 기억이 워킹 메모리에 주입됨 (컨텍스트 확장)

### 2.3 핀 메모리 (Pinned Memory)

유저가 "이거 잊지 마"라고 명시적으로 지정한 정보를 워킹 메모리에 강제 유지하는 슬롯.

- **등록:** 유저 명시적 요청 ("이거 기억해", "잊지 마" 등)
- **TTL:** 각 핀의 등록 시점 기준으로 개별 관리. 10턴 경과 시 해당 핀만 유지 확인 (v0.4.1: 복수 핀의 확인이 동시에 발생하지 않도록 분산)
- **해제 시:** 장기기억으로 이관, 이때 `pinned_flag = true` + 높은 초기 relevance score 부여
- **용량 제한:** 최대 3개 슬롯 (워킹 메모리 공간 침범 방지)
- **장기기억 내 특별 취급:** `pinned_flag = true`인 기억은 시간 감쇠가 50% 감소 (반감기 2배)

---

## 3. 태깅 시스템: 3축 구조

### 3.1 감정 축 (Emotion Axis)

Plutchik 8기본 감정 + 메타 태그 2개 = 10축.

| 축 | 설명 | 범위 |
|----|------|------|
| joy | 기쁨, 만족, 성취감 | 0.0 ~ 1.0 |
| sadness | 슬픔, 상실감, 실망 | 0.0 ~ 1.0 |
| anger | 분노, 좌절, 짜증 | 0.0 ~ 1.0 |
| fear | 두려움, 불안, 걱정 | 0.0 ~ 1.0 |
| surprise | 놀람, 의외성 | 0.0 ~ 1.0 |
| disgust | 혐오, 거부감 | 0.0 ~ 1.0 |
| trust | 신뢰, 안도, 친밀감 | 0.0 ~ 1.0 |
| anticipation | 기대, 흥미, 호기심 | 0.0 ~ 1.0 |
| importance | 메타: 주관적 중요도 | 0.0 ~ 1.0 |
| urgency | 메타: 시간적 긴급도 | 0.0 ~ 1.0 |

### 3.2 장면 축 (Scene Axis)

기억의 의미적 맥락을 분류하는 카테고리 태그.

| 태그 | 설명 |
|------|------|
| work | 업무, 프로젝트, 커리어 |
| relationship | 인간관계, 가족, 연애 |
| hobby | 취미, 창작, 엔터테인먼트 |
| health | 건강, 운동, 식사 |
| learning | 학습, 기술, 지식 |
| daily | 일상, 루틴, 생활 |
| philosophy | 사상, 가치관, 세계관 |
| meta | AI와의 관계, 시스템 관련 |

- 하나의 기억에 복수 장면 태그 허용 (최대 3개)
- 장면 태그는 감정과 독립적으로 검색 축 역할

### 3.3 시간 축 (Time Axis)

별도 태그가 아닌, 검색 시 relevance score에 곱해지는 감쇠 계수.

```
time_decay(days) = 0.5 ^ (days / half_life)
                 = exp(-days * ln(2) / half_life)
```

> **v0.4.1 수정:** 기존 `exp(-days / half_life)` 수식은 half_life 경과 시 0.368로 감소하여 실제 반감기가 아니었음. `0.5^(days/half_life)` 로 수정하여 half_life 경과 시 정확히 0.5로 감소하도록 보정.

| 기억 유형 | 반감기 (half_life) |
|-----------|-------------------|
| 일반 기억 | 30일 |
| 핀 해제 기억 (pinned_flag) | 60일 |
| 고빈도 인출 기억 (recall_count > 5) | 45일 |

---

## 4. 재태깅 메커니즘 (Reconsolidation)

### v0.4 핵심 변경: 블렌딩 금지, 강도 조절만 허용

v0.4 시뮬레이션에서 확인된 사항: 감정 벡터 블렌딩(두 벡터를 섞는 것)은 반복 시 모든 기억의 감정이 평균으로 수렴하여 검색 해상도가 붕괴한다. 재태깅은 감정의 **방향(어떤 감정인지)을 보존**하고 **강도(얼마나 강한지)만 조절**해야 한다.

### 재태깅 규칙

**기억이 recall되었을 때:**

1. **유저가 해당 기억을 실제 사용한 경우 (긍정 피드백)**
   - 감정 강도 감쇠율: 0.99 (거의 유지)
   - relevance_score += 0.1
   - recall_count += 1

2. **recall되었으나 유저가 무시한 경우 (부정 피드백)**
   - 감정 강도 감쇠율: 0.88 (빠른 감쇠)
   - 재분류 확률 35%: dominant 감정이 아닌 다른 감정축에 부스트 (+0.25)
   - 재분류 방향: 유저 선호 감정 방향 (exploitation) vs 랜덤 (exploration)

3. **중립 (피드백 불명확)**
   - 감정 강도 감쇠율: 0.95
   - 재분류 확률: exploration_rate (기본 15%)

### 재분류 시 dominant 전환 규칙

- 감정 방향 자체를 블렌딩하지 않는다
- 기존 감정값에 감쇠를 적용한 후, 새로운 감정축에 부스트를 가하는 방식
- dominant가 전환되면 해당 기억은 새로운 감정 카테고리에 재분류됨
- 이는 "슬픈 기억이 시간이 지나면 감사한 기억으로 재해석되는" 인간의 reconsolidation과 동일

---

## 5. 검색 알고리즘

### 5.1 복합 점수 계산

```python
def memory_score(memory, current_emotion, current_scene, current_time, user_prefs=None):
    # 1. 감정 유사도 (코사인 유사도, 0~1)
    emotion_sim = cosine_similarity(memory.emotion_vec, current_emotion)
    
    # 2. 장면 유사도 (Jaccard, 0~1)
    scene_sim = len(memory.scenes & current_scene) / max(len(memory.scenes | current_scene), 1)
    
    # 3. 시간 감쇠
    days_ago = (current_time - memory.timestamp).days
    half_life = 60 if memory.pinned_flag else (45 if memory.recall_count > 5 else 30)
    time_decay = math.exp(-days_ago / half_life)
    
    # 4. 피드백 가중치 (누적 relevance)
    feedback_weight = min(memory.relevance_score / 5.0, 2.0)  # cap at 2x
    
    # 4.5. 메타 스코어 (importance + urgency)
    meta_score = (memory.importance + memory.urgency) / 2.0

    # 복합 점수 (v0.4.1: 가중치 합 = 1.0으로 정규화)
    # emotion: 0.4, scene: 0.35, meta: 0.25
    base_score = emotion_sim * 0.4 + scene_sim * 0.35 + meta_score * 0.25
    score = base_score * time_decay * feedback_weight

    # 5. 다양성 보정 (exploration)
    if needs_exploration(current_results):
        score += random.uniform(0, 0.15)  # 예상 밖 기억 혼합

    return score
```

### 5.2 검색 프로세스

1. 백맨이 현재 대화에서 감정 벡터 + 장면 태그 추출
2. DB에서 감정 유사도 상위 후보 필터링 (Top-50)
3. 장면 유사도 + 시간 감쇠 + 피드백 가중치로 재정렬
4. 상위 3~5개를 워킹 메모리에 주입
5. 주입된 기억 중 유저가 실제 참조한 것을 피드백으로 기록

### 5.3 피드백 판정 방법 (v0.4.1 신규)

recall된 기억을 유저가 "사용했는지" 판정하는 3단계 방법:

**단계 1: 명시적 시그널 (확실)**
- 유저가 recall된 기억에 직접 언급 ("그때 그 일이지", "맞아 그랬어")
- 백맨이 유저 발화와 recall 내용의 의미적 유사도를 판정 (threshold: 0.6)

**단계 2: 암묵적 시그널 (추정)**
- recall 주입 후 대화가 해당 주제로 3턴 이상 지속 → 사용으로 추정
- 유저가 recall 내용을 무시하고 전혀 다른 주제로 전환 → 미사용으로 추정

**단계 3: 불명확 (중립 처리)**
- 위 두 단계로 판정 불가 시 → 중립 (§4 재태깅 규칙 3번 적용)

> MVP에서는 단계 1만 구현하고, 단계 2는 Phase 2에서 구현한다. 판정 로직은 백맨이 LLM 호출로 수행한다.

### 5.4 콜드 스타트 전략 (v0.4.1 신규)

장기기억이 비어 있거나 극소량인 초기 상태의 처리:

1. **검색 결과 0건:** 장기기억 검색을 스킵하고 워킹 메모리 + 핀만으로 응답
2. **초기 30턴:** 모든 이관 기억에 relevance_score = 1.5 부여 (초기 부스트)
3. **의미 있는 검색까지:** 장기기억 50건 이상 축적 시 정상 모드로 전환

### 5.5 세션 관리 (v0.4.1 신규)

- **세션 종료 시:** 워킹 메모리 잔여 턴을 즉시 장기기억으로 이관 (태깅 수행)
- **세션 시작 시:** 워킹 메모리는 빈 상태에서 시작, 직전 세션 마지막 3턴을 장기기억에서 검색하여 컨텍스트 힌트로 주입

---

## 6. 에코챔버 방지 (Diversity Watchdog)

### v0.4 신규: 시뮬레이션에서 확인된 위험

동일 감정 맥락의 기억만 반복 recall되면:
- 해당 감정의 기억만 강화 (긍정 피드백 루프)
- 다른 감정 카테고리의 기억이 감쇠로 사실상 사장
- 유저의 감정 선호가 변했을 때 대응할 seed가 없음

이는 PTSD에서 트라우마 기억이 같은 감정 맥락에서 반복 활성화되어 강화되는 것, 정치적 에코챔버에서 같은 프레임의 정보만 반복 소비되는 것과 동일한 메커니즘.

### 방지 메커니즘

**다양성 지표 모니터링:**

```python
def diversity_index(recent_recalls, window=50):
    """최근 N회 recall의 감정 카테고리 분포 (Shannon entropy)"""
    category_counts = Counter(get_dominant_emotion(r) for r in recent_recalls[-window:])
    total = sum(category_counts.values())
    entropy = -sum((c/total) * log2(c/total) for c in category_counts.values() if c > 0)
    return entropy / log2(8)  # 0~1 정규화, 1 = 완전 분산
```

**자동 exploration 조절:**

| diversity_index | exploration_rate | 의미 |
|-----------------|-----------------|------|
| > 0.7 | 8% (최소) | 건강한 분산, exploitation 우선 |
| 0.4 ~ 0.7 | 15% (기본) | 정상 범위 |
| < 0.4 | 30~50% (증가) | 편중 감지, 강제 다양화 |

**exploration 동작:** 검색 결과 Top-5 중 1~2개를 유저의 최근 recall과 다른 감정 카테고리에서 선택. "당신이 보통 안 보는 카테고리에서 이런 것도 있어요" 하는 세렌디피티 추천과 동일.

---

## 7. 데이터 구조

### SQLite 스키마

```sql
CREATE TABLE memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    raw_input TEXT,
    raw_response TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    -- 감정 벡터 (10축)
    joy REAL DEFAULT 0.0,
    sadness REAL DEFAULT 0.0,
    anger REAL DEFAULT 0.0,
    fear REAL DEFAULT 0.0,
    surprise REAL DEFAULT 0.0,
    disgust REAL DEFAULT 0.0,
    trust REAL DEFAULT 0.0,
    anticipation REAL DEFAULT 0.0,
    importance REAL DEFAULT 0.0,
    urgency REAL DEFAULT 0.0,
    
    -- 장면 태그 (JSON 배열)
    scenes TEXT DEFAULT '[]',
    
    -- 메타데이터
    relevance_score REAL DEFAULT 1.0,
    recall_count INTEGER DEFAULT 0,
    last_recalled DATETIME,
    pinned_flag BOOLEAN DEFAULT FALSE,
    
    -- 소프트 아카이브 (삭제 대신 비활성화)
    archived BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_importance ON memories(importance DESC);
CREATE INDEX idx_timestamp ON memories(timestamp DESC);
CREATE INDEX idx_relevance ON memories(relevance_score DESC);
CREATE INDEX idx_pinned ON memories(pinned_flag);

CREATE TABLE recall_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER REFERENCES memories(id),
    recalled_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    was_used BOOLEAN DEFAULT FALSE,
    dominant_emotion TEXT,
    context_scene TEXT
);
```

---

## 8. 시스템 아키텍처: 투 트랙 분업 (Dual-Agent)

### 프론트맨 (대화 담당)

- 사용자와 직접 소통
- 백맨이 주입한 컨텍스트(워킹메모리 + 장기기억 검색결과 + 핀)에만 의존
- API 교환형 (모델 선택 가능)

### 백맨 (기억 관리)

- 감정 + 장면 태깅
- 컨텍스트 윈도우 관리 (핀 TTL, 워킹메모리 롤링, 10턴 만료 이관)
- DB 검색 + 프롬프트 조립
- 피드백 수집 (유저가 recall된 기억을 사용했는지 판정)
- 다양성 모니터링 + exploration rate 조절

### 처리 흐름

```
유저 입력
    │
    ▼
백맨: 현재 입력의 감정 + 장면 분석
    │
    ▼
백맨: 장기기억 DB 검색 (감정 × 장면 × 시간 × 피드백)
    │
    ▼
백맨: 프롬프트 조립 [시스템 프롬프트 + 핀 메모리 + 검색 결과 + 워킹 메모리]
    │
    ▼
프론트맨: 응답 생성
    │
    ▼
백맨: 응답 분석 → 감정 태깅 → 워킹 메모리 갱신
    │
    ▼
(10턴 만료 시) 백맨: 장기기억으로 이관 + 태깅
(핀 만료 시) 백맨: 유저에게 유지 확인 → 해제 시 장기기억 이관
(recall된 기억) 백맨: 유저 사용 여부 판정 → 재태깅 + 피드백 기록
```

---

## 9. v0.4 시뮬레이션에서 도출된 설계 원칙

감정 벡터를 중력장으로 처리하는 공간 시뮬레이션을 7회 반복 실험한 결과 도출된 원칙:

### 구조적 제약

1. **상시 중력은 N체에서 반드시 붕괴한다** → 기억 간 상호작용은 이벤트 기반 검색으로만 처리
2. **감정 블렌딩은 반드시 수렴한다** → 재태깅 시 감정 방향은 보존, 강도만 조절
3. **Continuous 감정 공간 투영은 중앙 수렴 함정이 있다** → 감정 카테고리는 discrete (8개 기본 감정)
4. **최적화는 reward 없이 불가능하다** → 유저 피드백이 필수
5. **최적화만 하면 다양성이 죽는다** → exploration 보정 필수

### 병적 상태 모델

- **정상:** recall 시 감정 감쇠 + 저확률 재분류 → 기억이 카테고리 간 유동
- **병적:** dominant 강화 + 재분류 정지 → 특정 카테고리에 고착 (에코챔버/반추)
- **치료:** 병적 상태 감지 시 강제 exploration 증가 (다른 맥락 주입)
- **전환:** 정상 ↔ 병적의 차이는 파라미터 하나 (재분류 확률)

### 시뮬레이션이 기각한 접근법

| 접근법 | 기각 이유 |
|--------|-----------|
| 상시 중력 시뮬레이션 | N체 문제로 블랙홀화 필연 |
| 레너드-존스 평형 모델 | 당구공화 (연상 검색 기능 상실) |
| 고정 앵커 + 리시 | 중앙 수렴 (앵커 배치에 구조적 의미 없음) |
| 감정 벡터 원형 투영 | 혼합 감정이 항상 중앙으로 매핑 |
| 감정 블렌딩 재태깅 | 반복 시 모든 벡터가 평균 수렴 |

---

## 10. 로드맵

### Phase 1: MVP (현재 목표)

- 워킹 메모리 10턴 + 장기기억 DB (SQLite)
- 감정 10축 + 장면 태그 + 시간 감쇠
- 핀 메모리 (최대 3슬롯)
- 기본 검색 (감정 유사도 × 장면 유사도 × 시간 감쇠)
- Dual-Agent (프론트맨 + 백맨)

### Phase 2: 피드백 루프

- 유저 피드백 수집 (recall된 기억의 사용 여부 판정)
- 피드백 기반 재태깅 (강도 조절 + 재분류)
- 다양성 모니터링 + exploration 자동 조절
- 빈도 가중치 (recall_count 기반 반감기 조절)

### Phase 3: 최적화

- 백맨을 sLM으로 교체 (비용 최적화)
- 태깅 정확도 벤치마크 + 튜닝
- 페르소나 다이얼 시스템 (감정 가중치 프리셋으로 성격 전환)
- Shogun / ITW 시스템과의 통합

---

## 부록 A: 생물학적 대응 관계

| 뇌 메커니즘 | 시스템 대응 | 구현 |
|-------------|-----------|------|
| 워킹 메모리 (전전두엽) | 워킹 메모리 10턴 | 원본 보존, FIFO |
| 해마 → 신피질 전이 | 워킹 → 장기기억 이관 | 10턴 만료 시 태깅 + 압축 |
| 재고정화 (reconsolidation) | 재태깅 | recall 시 강도 조절 (블렌딩 금지) |
| 수면 시냅스 항상성 (SHY) | 시간 감쇠 | exp(-days/half_life) |
| 측면 억제 (lateral inhibition) | 다양성 보정 | exploration으로 카테고리 분산 |
| 전전두엽 능동적 유지 | 핀 메모리 | 유저 명시적 지정, TTL 관리 |
| PTSD 동결 기억 | 에코챔버 감지 | diversity < 0.4 시 exploration 증가 |
| 치료적 재처리 (EMDR 등) | 강제 다양화 | 다른 맥락의 기억 주입 |

## 부록 B: 감정 태깅 프롬프트 (백맨용)

```
다음 대화 내용의 감정과 장면을 분석하여 JSON으로 출력하라.

감정: 각 값은 0.0~1.0. 해당 감정이 없으면 0.0.
장면: work, relationship, hobby, health, learning, daily, philosophy, meta 중 해당하는 것 (최대 3개).

반드시 JSON만 출력하고 다른 텍스트는 포함하지 마라.

대화 내용:
"{input}"
```

기대 출력:

```json
{
  "emotion": {
    "joy": 0.0,
    "sadness": 0.6,
    "anger": 0.2,
    "fear": 0.0,
    "surprise": 0.3,
    "disgust": 0.1,
    "trust": 0.0,
    "anticipation": 0.0,
    "importance": 0.9,
    "urgency": 0.2
  },
  "scenes": ["work", "philosophy"]
}
```

---

*작성일: 2026-03-05*
*작성자: 노부 + Claude Opus 4.6*
*버전: 0.4 (중력장 시뮬레이션 결과 반영)*
