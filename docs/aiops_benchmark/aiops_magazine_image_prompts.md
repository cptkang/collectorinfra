# AIOps INSIGHT 매거진 — 이미지 생성 프롬프트 시트

> 목적: 매거진(`aiops_finance_magazine.html`)의 각 섹션에 삽입할 **사진/비주얼**을 외부 이미지 생성 모델(Midjourney · DALL·E 3 · Google Imagen · Stable Diffusion 등)에서 생성하기 위한 프롬프트 모음.

> 프롬프트는 영문(모델 성능 최적)이며, 각 슬롯에 한글 의도·배치·비율·네거티브 프롬프트를 함께 제공한다.


## 공통 아트 디렉션 (모든 이미지 일관성 유지)

**모든 프롬프트는 "일러스트/렌더"가 아닌 진짜 사진(real photograph)을 목표로 한다.**

> **⚠ 왜 "생성된 이미지처럼" 보이나 — 핵심 원인.** `8k · ultra sharp · hyperrealistic · cinematic · high detail · ultra-detailed · masterpiece` 같은 단어는 역설적으로 **매끈하고 인공적인 CG/AI 룩**을 강하게 유발한다. 실사는 정반대로 유도해야 한다 — **구체적 카메라 바디·렌즈·조리개·ISO·필름스톡 + 자연스러운 결함(그레인·약간의 비대칭·먼지·반사)** + 스냅샷/다큐 감성.

아래 개선된 스타일 앵커를 모든 프롬프트 끝에 붙인다:

```
STYLE ANCHOR (append to every prompt):
candid real photograph, shot on Canon EOS R5 with 35mm f1.8 lens, ISO 200,
natural available light, realistic imperfect details, subtle film grain,
true-to-life color, slight lens vignette, documentary editorial style,
authentic unretouched look, navy structural accents with amber and a single
emerald-green highlight, negative space for text
```
- **실사 유도 4원칙**: ① 카메라 바디+렌즈+조리개+ISO를 구체적으로(모델이 EXIF 있는 사진으로 인식), ② `candid · documentary · unretouched · snapshot`으로 연출 티 제거, ③ `subtle film grain · realistic imperfect details · slight vignette`로 자연스러운 결함 부여, ④ **`8k·hyperrealistic·cinematic·ultra sharp·masterpiece`는 절대 쓰지 않는다**.
- **공통 네거티브 (모든 슬롯에 추가·강화)**: `cgi, 3d render, cg, octane render, unreal engine, artstation, digital art, illustration, painting, cartoon, anime, hyperrealistic, over-processed, airbrushed, plastic skin, waxy, overly smooth, perfect, glossy, over-saturated, HDR halo, ai-generated look, dark, underexposed, blurry, distorted, extra fingers, text, logos, watermark`
- **일관성 팁**: Midjourney는 `--style raw --stylize 50`(낮은 stylize가 더 사실적) + `--sref`/`--seed`로, DALL·E/Imagen은 위 앵커를 반복. SDXL은 사진계열 체크포인트(예: RealVis/Juggernaut) 권장.
- **텍스트 금지**: 이미지 모델은 글자를 왜곡하므로 이미지엔 텍스트를 넣지 않고, 제목·수치는 매거진 HTML에서 오버레이한다.
- **비율**: 세로 매거진 본문 폭(720px)에 맞춰 대부분 3:2 가로. 표지·배너는 필요 시 16:9 또는 4:5.


---

## 01 · 표지 히어로 (Cover Hero)
**섹션**: Cover  ·  **권장 비율**: 3:2 가로 (또는 16:9)

**역할/의도**: 수천 개의 알람이 AIOps 지능을 거쳐 안정된 금융 거래로 수렴하는 흐름

**한글 설명**: **밝고 선명한 주광(daylight)** 아래 현대적 금융 데이터센터의 서버랙 통로가 배경으로 또렷하게 보이는 **실사 사진**. 왼쪽에서 은은한 앰버 빛줄기(알람 신호)가 중앙으로 모여 오른쪽에서 매끄러운 에메랄드 광선(안정된 거래 흐름)으로 이어진다. 광효과는 **배경을 가리지 않는 은은한 오버레이** 수준으로만.

**영문 프롬프트 (복사용)**:
```
A bright candid wide-angle photograph of a modern financial data center interior, clean rows of illuminated server racks receding down a well-lit corridor clearly visible, bright even daytime lighting with soft daylight from large windows, airy and open atmosphere, professional architectural photography, shot on Canon EOS R5 24mm f4 ISO 200, natural available light, well-exposed. Subtle translucent light effects overlaid without obscuring the scene: faint amber light streaks flowing in from the left, a soft glow in the center, and a smooth calm emerald-green light ribbon flowing out to the right symbolizing stable transactions,
editorial magazine cover photography, premium corporate finance aesthetic, predominantly light and bright tones with navy blue structural accents and amber-gold plus a single emerald-green highlight, natural bright lighting, clean composition with negative space at top for title text, RAW photo, shot on Canon EOS R5 35mm f2.2 ISO 200, natural daylight, subtle film grain, realistic imperfections, candid documentary look
```

**네거티브 프롬프트**: `dark, underexposed, murky, dim, low-key lighting, heavy shadows, black background, night scene, foggy, blurry background, text, logos, readable letters, people faces, clutter, cartoon, illustration, 3d render, watermark`

**비고**: 현재 mag_hero.png(도식) 대체용. **개선 포인트 — ① at night/dark → bright daytime·well-lit·well-exposed로 명도 대폭 상향, ② 배경(서버랙 통로)을 sharp focus·clearly visible로 명시해 가독성 확보, ③ 광입자를 배경을 가리지 않는 translucent overlay로 축소, ④ 카메라 바디+렌즈+ISO+film grain으로 실사 강조(8k·hyperrealistic 금지), ⑤ 네거티브에 dark/underexposed/night 계열 추가.** 색조는 밝은 톤 우세 + 네이비는 구조색 액센트로. 여전히 어두우면 프롬프트 앞에 `bright, high-key lighting,` 추가하거나 생성 후 노출·밝기를 +15~20% 보정.

---

## 02 · 특집 — 왜 지금 (Feature)
**섹션**: Feature · 특집  ·  **권장 비율**: 3:2 가로

**역할/의도**: 알람 폭증 속 운영자의 통증 — 관제실 오버로드

**한글 설명**: 잘 갖춰진 현대적 IT 관제실(NOC)의 **실사 사진**. 대형 모니터 월에 수많은 대시보드·그래프가 떠 있고, 운영자 한 명이 뒷모습으로 화면을 마주해 정보 과부하를 암시. 실내는 또렷하게 보이되 붉은 경고 반사광으로 긴장감 표현.

**영문 프롬프트 (복사용)**:
```
A realistic documentary photograph of a modern, well-lit IT network operations center (NOC), a large video wall of monitors displaying numerous dashboards and cascading graphs, one operator seen from behind facing the screens conveying information overload, the room interior clearly visible with clean desks and ambient lighting, subtle red alert reflections on surfaces for tension, natural office lighting balanced with screen glow, candid photojournalism style, shot on full-frame DSLR 35mm lens f2.8,
RAW photo, natural realistic lighting, bright well-exposed, editorial magazine photography, premium corporate finance aesthetic, navy accents with amber and red highlights, subtle film grain, candid photojournalism, authentic unretouched look
```

**네거티브 프롬프트**: `공통 네거티브 +` `readable text on screens, brand logos, identifiable faces, staged stock-photo look`

**비고**: 인물은 뒷모습 권장(초상권·범용성). 야간→"well-lit·room clearly visible"로 배경 가독성 확보하되 붉은 반사광으로 '통증' 유지.

---

## 03 · 노이즈 캔슬링 (Signal)
**섹션**: Signal · 노이즈 캔슬링  ·  **권장 비율**: 3:2 또는 2:1 가로

**역할/의도**: 노이즈(혼돈) → 명료함(질서) 대비

**한글 설명**: 운영자 책상 위 **실사 사진** — 좌측 화면은 붉은 경고가 빼곡한 혼잡한 대시보드, 우측 화면은 초록 정상 상태의 깔끔한 대시보드. 정돈된 데스크 환경에서 노이즈 감소 전/후를 한 프레임에 대비.

**영문 프롬프트 (복사용)**:
```
A realistic close-up photograph of two computer monitors side by side on a clean modern operations desk, the left monitor crowded with dense red alert notifications and cluttered dashboards, the right monitor calm and organized showing a few green healthy status panels, clear contrast of chaos versus clarity, bright office lighting, shallow depth of field focusing on the screens, realistic screen glow and desk materials, shot on full-frame DSLR 50mm lens f2.0,
RAW photo, natural realistic lighting, bright well-exposed, editorial tech photography, red-to-green contrast with navy surroundings, RAW photo, subtle film grain, realistic screen glare and dust, candid look
```

**네거티브 프롬프트**: `공통 네거티브 +` `readable specific text, brand logos, abstract data-art look, glowing particles`

**비고**: 현재 mag_noise.png(산점도)는 **데이터 도식이라 유지 권장**. 이 실사 사진은 Signal 섹션 도입 배너용 보조. 좌 붉은 혼돈 → 우 녹색 질서.

---

## 04 · 심층 사례연구 (Case Study)
**섹션**: Case Study  ·  **권장 비율**: 3:2 가로

**역할/의도**: 은행/금융 기업의 신뢰·안정 이미지

**한글 설명**: 현대적인 대형 은행 본사 로비 또는 금융 트레이딩 플로어의 세련된 실사. 유리·대리석의 고급스러운 질감, 안정감과 신뢰를 상징. 인물은 배경 실루엣 정도

**영문 프롬프트 (복사용)**:
```
A realistic architectural photograph of a modern global bank headquarters lobby, sleek glass walls and polished marble floors, bright and airy interior flooded with natural daylight from tall windows, elegant corporate atmosphere conveying trust and stability, a few softly blurred professionals walking in the background, high-end financial institution interior, wide-angle real estate photography, shot on full-frame DSLR 24mm lens,
RAW photo, natural realistic lighting, bright well-exposed, candid real estate photography, clean navy and warm marble tones, RAW photo, subtle film grain, natural imperfect reflections
```

**네거티브 프롬프트**: `공통 네거티브 +` `readable signage, brand logos, identifiable faces, stock-photo cliché`

**비고**: TD Bank·BMO 사례 섹션 상단 배너용. 밝은 자연광으로 신뢰·안정감 강조.

---

## 05 · 산업별 성과 (Gallery)
**섹션**: Gallery  ·  **권장 비율**: 3:2 가로

**역할/의도**: 통신·미디어·IT서비스·공공 등 산업 다양성

**한글 설명**: 여러 산업 인프라가 한 프레임에 담긴 **실사 사진** — 통신 타워·데이터센터·도심 오피스가 어우러진 도시 인프라 스카이라인. 몽타주(합성)보다 한 장의 자연스러운 실사 도시 풍경으로.

**영문 프롬프트 (복사용)**:
```
A realistic wide cityscape photograph capturing modern infrastructure in one frame — telecom towers, a data center building, and corporate office towers across a bright urban skyline under clear daylight, conveying diverse industries connected by technology, crisp clear atmosphere, aerial drone photography perspective, natural bright daylight, shot on full-frame DSLR 35mm lens,
RAW photo, natural realistic lighting, bright well-exposed, candid documentary photography, clean bright tones with navy sky accents, RAW photo, subtle atmospheric haze, realistic film grain
```

**네거티브 프롬프트**: `공통 네거티브 +` `collage seams, composite montage look, brand logos, crowded chaos`

**비고**: 11개 비금융 사례 갤러리 상단 배너용. 합성 몽타주 대신 단일 실사 도시 풍경으로 자연스럽게.

---

## 06 · 적용 플레이북 (Playbook)
**섹션**: Playbook  ·  **권장 비율**: 3:2 가로 또는 세로 4:5

**역할/의도**: 단계적 전진·로드맵·팀 협업

**한글 설명**: 상승하는 계단 또는 경로 위에 단계별 이정표가 놓인 미니멀 컨셉 실사. 점진적 도입(crawl-walk-run)과 로드맵을 은유. 네이비·골드 톤

**영문 프롬프트 (복사용)**:
```
A realistic architectural photograph of a bright modern staircase ascending toward large windows with daylight streaming in, clean minimalist design with warm wood and stone steps, sense of upward progression and forward momentum, an empty airy interior space, natural bright lighting, shot on full-frame DSLR 35mm lens,
RAW photo, natural realistic lighting, bright well-exposed, candid architecture photography, clean navy and warm gold tones, RAW photo, subtle film grain, natural imperfect shadows
```

**네거티브 프롬프트**: `공통 네거티브 +` `glowing markers, conceptual cg overlay, people faces, clutter`

**비고**: 현재 mag_roadmap.png(타임라인 도식)은 **데이터 도식이라 유지 권장**. 이 실사 사진은 Playbook 섹션 도입 배너용 보조(상승 계단=점진 도입 은유).

---

## 07 · 실행 조건 (Guardrails)
**섹션**: Guardrails  ·  **권장 비율**: 3:2 가로

**역할/의도**: 규제·보안·거버넌스 — 폐쇄망·설명가능성·통제

**한글 설명**: 보안·통제를 상징하는 **실사 사진** — 통제된 출입 게이트가 있는 정돈된 데이터센터 보안구역, 또는 격리된 서버룸. 자물쇠 클리셰 대신 실제 시설 사진으로 견고함·신뢰 표현.

**영문 프롬프트 (복사용)**:
```
A realistic photograph of a secure data center access area, a clean organized server room behind a controlled glass security door with a card-access panel, well-lit corridor, conveying compliance, auditability and robustness of an isolated closed network, bright clean facility lighting, professional facility photography, shot on full-frame DSLR 28mm lens,
RAW photo, natural realistic lighting, bright well-exposed, candid facility photography, cool navy tones with brushed metal accents, RAW photo, subtle film grain, realistic fingerprints and dust on glass
```

**네거티브 프롬프트**: `공통 네거티브 +` `cliché padlock or shield icon, glowing hologram, people faces, brand logos`

**비고**: 망분리·설명가능성·DORA 섹션 상단 배너용. 아이콘 클리셰 대신 실제 보안시설 사진으로.

---

## 08 · 결론 (Editorial)
**섹션**: Editorial  ·  **권장 비율**: 3:2 가로

**역할/의도**: 안정·성숙·미래 지향의 마무리 톤

**한글 설명**: 동트는 도시 금융지구 스카이라인 또는 안정적으로 흐르는 데이터 광선의 서정적 실사. 성취·안정·다음 단계를 암시하는 밝고 희망적인 톤

**영문 프롬프트 (복사용)**:
```
A realistic photograph of a financial district city skyline at sunrise, clear bright golden morning light bathing modern glass skyscrapers, calm hopeful atmosphere conveying achievement and a forward-looking future, crisp clear sky, professional cityscape photography, shot on full-frame DSLR 50mm lens,
RAW photo, natural realistic lighting, bright well-exposed, candid cityscape photography, warm golden and navy sky tones, RAW photo, subtle film grain, natural atmospheric haze
```

**네거티브 프롬프트**: `공통 네거티브 +` `flowing data light streams, cg overlay, people faces, dark gloomy mood, brand logos`

**비고**: 결론 섹션 마무리 배너용. 새벽 골든아워 실사 스카이라인으로 밝고 희망적인 마무리.


---

## 모델별 사용 팁

| 모델 | 비율 지정 | 텍스트/네거티브 처리 |
|---|---|---|
| **Midjourney v6** | `--ar 3:2` (표지 `--ar 16:9`) | 네거티브는 `--no text, logos, faces`. 스타일 통일은 `--sref`/`--seed` |
| **DALL·E 3 (ChatGPT)** | 프롬프트에 "wide 3:2 aspect" 명시 | 네거티브 미지원 → 프롬프트에 "without any text or logos" 포함 |
| **Google Imagen 3** | UI에서 비율 선택 | STYLE ANCHOR 반복으로 톤 통일 |
| **Stable Diffusion XL** | width/height로 3:2(예 1536×1024) | Negative prompt 필드에 네거티브 그대로 입력 |

## 삽입 위치 매핑 (매거진 HTML)

| 슬롯 | 대체/추가 대상 | 현재 상태 |
|---|---|---|
| 01 표지 히어로 | `mag_hero.png` | 데이터 도식 → 사진 대체 가능 |
| 02 특집 | (신규) Feature 섹션 상단 | 현재 이미지 없음 → 추가 |
| 03 노이즈 | (보조) `mag_noise.png`(산점도)는 데이터 도식이라 유지 | 실사 배너 보조 추가 |
| 04 사례연구 | (신규) Case Study 배너 | 현재 이미지 없음 → 추가 |
| 05 갤러리 | (신규) Gallery 배너 | 현재 이미지 없음 → 추가 |
| 06 플레이북 | (보조) `mag_roadmap.png`은 데이터 도식이라 유지 | 배너 보조용 |
| 07 실행조건 | (신규) Guardrails 배너 | 현재 이미지 없음 → 추가 |
| 08 결론 | (신규) Editorial 마무리 | 현재 이미지 없음 → 추가 |

> **주의**: 로드맵(06)·효과차트는 **수치 데이터 도식**이므로 사진으로 대체하지 말고 현행 유지를 권장한다(데이터 정확성). 사진은 섹션 도입 배너·표지·은유 이미지에 사용한다.