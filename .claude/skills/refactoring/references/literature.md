# 리팩터링 근거 문헌

조사일 2026-10-14. 서지(저자·제목·게재처·권호·DOI/ISBN)는 Crossref API·doi.org 내용협상·Open Library API로 대조했다. **「요지」 칸은 본문 대조가 아니라 널리 알려진 내용의 요약이다** — 특정 수치·문장을 인용하려면 원문을 먼저 확인한다. 인용수는 근거로 쓰지 않는다(OpenAlex는 AI·SE 분야를 과소집계한다).

층: **P** 동료심사 · **B** 단행본 · **S** 표준·PEP

## 이 스킬의 규칙 ↔ 근거

| 스킬 규칙 | 근거 |
|---|---|
| 리팩터 = 동작 보존 · 작은 단계마다 테스트 | Fowler 2018 · Mens & Tourwé 2004 |
| 특성화 테스트 먼저 · seam(patch 지점) 보존 | Feathers 2004 |
| 이동과 기능 변경을 한 커밋에 섞지 않는다 | Murphy-Hill 외 2012(실무자는 섞는 경향 — 그래서 규칙으로 막는다) |
| 이동·분해는 작게, 회귀 위험을 게이트로 막는다 | Bavota 외 2012 · Kim 외 2012/2014 |
| 스멜은 측정으로 고르고 우선순위를 둔다 | Palomba 외 2018 · Khomh 외 2012 · Sharma & Spinellis 2018 |
| 스멜은 기능 추가 시점에 들어온다 → 기능 계획에서도 규약을 권고 | Tufano 외 2017 |
| 복제를 무조건 합치지 않는다 | Kapser & Godfrey 2008 |
| 모듈은 바뀔 결정을 숨기는 단위로 나눈다(처리 순서로 나누지 않는다) | Parnas 1972 |
| 깊은 모듈 선호 · 얕은 래퍼·클래스 과다 분할 경계 | Ousterhout 2018 |
| 패턴은 어휘이지 목표가 아니다 · 효과는 일관되지 않다 | Gamma 외 1994 · Wedyan & Abufakher 2020 |
| SRP·OCP·LSP·ISP·DIP | Martin 2002 |
| SSOT/DRY — 지식의 표현은 하나 | Hunt & Thomas 1999 |
| 근사 cc를 특성화 부담의 지표로 | McCabe 1976 |
| 이동 shim·별칭(모듈 `__getattr__`로 폐기 경고 가능) | PEP 562 |
| 리팩터와 보안 수정을 섞지 않는다 · 리팩터 뒤에도 보안 게이트 | Iannone 외 2023 |

## 목록

| 서지 | 층 | 요지 |
|---|---|---|
| Fowler, M. (2018). *Refactoring: Improving the Design of Existing Code* (2nd ed.). Addison-Wesley. ISBN 978-0-13-475759-9 | B | 스멜 카탈로그(Long Function·Duplicated Code·Large Class·Divergent Change·Shotgun Surgery·Feature Envy·Primitive Obsession 등)와 동작 보존 리팩터 기법 목록. 작은 단계마다 테스트. 2판 예제는 JavaScript |
| Feathers, M. C. (2004). *Working Effectively with Legacy Code*. Prentice Hall. ISBN 978-0-13-117705-5 | B | 테스트 없는 코드 = 레거시. 변경 전에 현재 동작을 고정하는 특성화 테스트, 코드를 고치지 않고 동작을 바꿔 끼울 수 있는 이음매(seam) |
| Gamma, E., Helm, R., Johnson, R., & Vlissides, J. (1994). *Design Patterns: Elements of Reusable Object-Oriented Software*. Addison-Wesley. ISBN 978-0-201-63361-0 (저작권 1994 · 출간 1995 표기 혼재) | B | 반복 설계 문제의 이름 붙은 해법 23종 |
| Martin, R. C. (2002). *Agile Software Development, Principles, Patterns, and Practices*. Prentice Hall. ISBN 978-0-13-597444-5 | B | SOLID 원칙과 패키지 원칙(비순환 의존 ADP 포함)의 단행본 정리 |
| Hunt, A., & Thomas, D. (1999). *The Pragmatic Programmer*. Addison-Wesley. ISBN 978-0-201-61622-4 (Open Library는 2000 표기 · 20주년판 2019 ISBN 978-0-13-595705-9) | B | DRY — 모든 지식은 시스템 안에 단 하나의 권위 있는 표현을 가진다 |
| Ousterhout, J. (2018). *A Philosophy of Software Design*. Yaknyam Press. ISBN 978-1-7321022-0-0 (2판 2021 ISBN 978-1-7321022-1-7) | B | 깊은 모듈(좁은 인터페이스·풍부한 기능) 선호, 얕은 모듈과 클래스 과다 분할은 복잡도를 높인다 |
| Parnas, D. L. (1972). On the criteria to be used in decomposing systems into modules. *Communications of the ACM, 15*(12), 1053–1058. doi:10.1145/361598.361623 | P | 정보 은닉 — 변경될 설계 결정을 숨기는 단위로 분해 |
| McCabe, T. J. (1976). A complexity measure. *IEEE TSE, SE-2*(4), 308–320. doi:10.1109/TSE.1976.233837 | P | 순환 복잡도 = 제어 흐름 그래프의 독립 경로 수(기저 경로 테스트 수) |
| Mens, T., & Tourwé, T. (2004). A survey of software refactoring. *IEEE TSE, 30*(2), 126–139. doi:10.1109/TSE.2004.1265817 | P | 리팩터 활동·기법·도구·동작 보존 형식화 서베이 |
| Khomh, F., Di Penta, M., Guéhéneuc, Y.-G., & Antoniol, G. (2012). An exploratory study of the impact of antipatterns on class change- and fault-proneness. *Empirical Software Engineering, 17*(3), 243–275. doi:10.1007/s10664-011-9171-y | P | 안티패턴(Blob 등)에 속한 클래스는 변경·결함이 더 잦다 |
| Kim, M., Zimmermann, T., & Nagappan, N. (2012). A field study of refactoring challenges and benefits. *Proc. FSE 2012*, 1–11. doi:10.1145/2393596.2393655 | P | Microsoft 실무자 조사 — 이점(가독성·유지보수성)과 위험(회귀 버그·병합 충돌), 대규모 리팩터의 도구·테스트 지원 부족 |
| Kim, M., Zimmermann, T., & Nagappan, N. (2014). An empirical study of refactoring challenges and benefits at Microsoft. *IEEE TSE, 40*(7), 633–649. doi:10.1109/TSE.2014.2318734 | P | 위의 확장판 — 정량 분석 추가 |
| Murphy-Hill, E., Parnin, C., & Black, A. P. (2012). How we refactor, and how we know it. *IEEE TSE, 38*(1), 5–18. doi:10.1109/TSE.2011.41 (ICSE 2009판 doi:10.1109/ICSE.2009.5070529) | P | 실무자는 리팩터를 다른 변경과 섞어 수행하는 경우가 많고 자동화 도구 사용은 적다 |
| Bavota, G., De Carluccio, B., De Lucia, A., Di Penta, M., Oliveto, R., & Strollo, O. (2012). When does a refactoring induce bugs? An empirical study. *Proc. SCAM 2012*. doi:10.1109/SCAM.2012.20 (뒤 두 저자는 Crossref 결과에 표시되지 않음 — 인용 전 확인) | P | 리팩터 일부가 결함을 유발했고 유형별로 위험이 다르다(상속 계층·메서드 이동 계열 주의) |
| Tufano, M., Palomba, F., Bavota, G., Oliveto, R., Di Penta, M., De Lucia, A., & Poshyvanyk, D. (2017). When and why your code starts to smell bad (and whether the smells go away). *IEEE TSE, 43*(11), 1063–1088. doi:10.1109/TSE.2017.2653105 (ICSE 2015판 doi:10.1109/ICSE.2015.59) | P | 스멜은 대개 코드가 처음 만들어질 때·기능 추가 중에 들어오고 리팩터 없이 남는다 |
| Palomba, F., Bavota, G., Di Penta, M., Fasano, F., Oliveto, R., & De Lucia, A. (2018). On the diffuseness and the impact on maintainability of code smells: A large scale empirical investigation. *Empirical Software Engineering, 23*(3), 1188–1221. doi:10.1007/s10664-017-9535-z | P | 스멜 종류마다 변경·결함 경향에 미치는 영향이 다르다 → 우선순위가 필요하다 |
| Sharma, T., & Spinellis, D. (2018). A survey on software smells. *Journal of Systems and Software, 138*, 158–173. doi:10.1016/j.jss.2017.12.034 | P | 스멜 정의·분류·탐지 서베이 — 용어 출처 |
| Kapser, C. J., & Godfrey, M. W. (2008). "Cloning considered harmful" considered harmful: Patterns of cloning in software. *Empirical Software Engineering, 13*(6), 645–692. doi:10.1007/s10664-008-9076-6 | P | 복제가 모두 해롭지는 않다(포크·플랫폼 변형 등 합리적 패턴) |
| Wedyan, F., & Abufakher, S. (2020). Impact of design patterns on software quality: A systematic literature review. *IET Software, 14*(1), 1–17. doi:10.1049/iet-sen.2018.5446 | P | 패턴의 품질 효과는 일관되지 않다 — 무조건 적용할 근거는 약하다 |
| Iannone, E., Codabux, Z., Lenarduzzi, V., De Lucia, A., & Palomba, F. (2023). Rubbing salt in the wound? A large-scale investigation into the effects of refactoring on security. *Empirical Software Engineering*. doi:10.1007/s10664-023-10287-x | P | 39개 프로젝트 리팩터 커밋 분석 — 리팩터와 보안 지표의 연관은 제한적이고, 일부 유형(Extract & Move Method 등)은 취약점 도입 커밋에 더 자주 등장(본문 미열람 · 요약 기반) |
| Levkivskyi, I. PEP 562 — Module `__getattr__` and `__dir__` (Python 3.7). https://peps.python.org/pep-0562/ | S | 모듈 수준 `__getattr__` — 지연 재노출·폐기 경고 |

## 조사 공백

- Python 같은 동적 언어의 리팩터 안전성(동적 속성 접근·monkeypatch 추적)을 직접 다룬 동료심사 문헌은 이번 조사에서 확인하지 못했다. 이 스킬의 seam 보존 규칙(D-330 ②)은 Feathers의 이음매 개념과 저장소 실측(`apm_query._SESSION_FACTORY` 38파일 52곳)에 근거한다.
- 특성화 테스트는 사실상 단행본(Feathers)이 유일한 출처다.
- 복제 제거의 위험은 Kapser & Godfrey 한 편만 확인했다.

## 인용하지 않기로 한 것

- Martin, "Design Principles and Design Patterns"(2000) — 서지 레코드 미확인.
- Feldthaus 외(2011) OOPSLA JavaScript 리팩터링 — 저자 목록 미확인.
