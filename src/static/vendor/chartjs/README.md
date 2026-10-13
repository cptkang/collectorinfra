# Chart.js (벤더링 사본)

조회 결과 「그래프로 보기」 패널(`plans/160` · D-327)의 선·막대 그래프용이다. 폐쇄망에서는 CDN을 쓸 수 없어 UMD 빌드 1파일만 저장소에 둔다.

- 패키지: `chart.js@4.5.1`(npm) · 라이선스 MIT(`LICENSE.md`)
- 반입: 2026-10-13 개발 맥에서 `npm pack chart.js@4`로 받아 `dist/chart.umd.min.js`만 복사했다(수정 없음). tarball 무결성은 npm 레지스트리 `dist.integrity`(sha512 `GIjfiT9d…E8/Pw==`)와 일치를 확인했다.
- 날짜 어댑터는 반입하지 않았다 — 시간 축은 epoch ms 선형 축 + 눈금 서식 함수로 그린다.
- 화면은 `<script src="/static/vendor/chartjs/chart.umd.min.js">`로 읽고 전역 `Chart`를 쓴다.

| 파일 | sha256 |
|---|---|
| `chart.umd.min.js` | `48444a82d4edcb5bec0f1965faacdde18d9c17db3063d042abada2f705c9f54a` |
