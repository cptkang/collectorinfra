# 데이터 발생용 샘플 WAS — 제니퍼 Java 에이전트는 기동 시 로컬 뷰 서버에서 받는다(공식 샘플 방식)
# 뷰 서버 5.7.0.1이 배포하는 에이전트는 5.5.2.5라 JDK 17에서 JVM 기동이 실패한다(2026-09-29 실측) → JDK 11.
# 최신 에이전트(JDK 17+)를 확보하면 jdk17로 올린다(plans/87 §0.10).
FROM tomcat:9.0-jdk11-temurin
COPY entrypoint.sh /usr/local/bin/was-entrypoint.sh
EXPOSE 8080
ENTRYPOINT ["sh", "/usr/local/bin/was-entrypoint.sh"]
