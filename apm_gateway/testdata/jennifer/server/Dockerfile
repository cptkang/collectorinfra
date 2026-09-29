# 제니퍼 데이터 서버 + 뷰 서버 (한 컨테이너 · 로컬 검증 전용)
# 공식 샘플(jennifer-container-sample)은 JDK 8 기반이라 5.6.5+에 쓸 수 없다 → JDK 21.
FROM eclipse-temurin:21-jdk
COPY entrypoint.sh /usr/local/bin/jennifer-entrypoint.sh
EXPOSE 7900 5000
ENTRYPOINT ["sh", "/usr/local/bin/jennifer-entrypoint.sh"]
