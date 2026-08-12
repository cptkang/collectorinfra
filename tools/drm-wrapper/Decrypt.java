// Softcamp ServiceLinker 복호화 CLI 래퍼 (Plan 69 §3.4 — 단일 소스 실행, 빌드 없음)
//
// 실행: java -cp <scsl.jar> Decrypt.java <in> <out> <properties> <keyfile> <groupId>
//   - JDK 11+ single-file source launch (운영 반입 JDK 21 지원, scsl 요구는 Java 1.8+)
//   - stdout에 "RET:<n>" 출력 (n = CreateDecryptFileDAC 반환값)
//   - exit 0: ret ∈ {0, -36} (가이드: -36=원본이 평문, 성공과 동일 취급), 그 외 exit 1
//   - 에러 분류·재시도·로그는 Python 쪽(ScslCliDecryptor) 책임 — 여기는 호출만 한다.
import SCSL.SLDsFile;

public final class Decrypt {
    public static void main(String[] args) {
        if (args.length != 5) {
            System.out.println("RET:2001"); // INVALID_PARAMETER
            System.err.println("usage: Decrypt <in> <out> <properties> <keyfile> <groupId>");
            System.exit(1);
        }
        int ret;
        try {
            SLDsFile sFile = new SLDsFile();
            sFile.SettingPathForProperty(args[2]);
            ret = sFile.CreateDecryptFileDAC(args[3], args[4], args[0], args[1]);
        } catch (Throwable t) {
            System.out.println("RET:-61"); // ERROR_EXCEPTION
            System.err.println("EXCEPTION: " + t);
            System.exit(1);
            return;
        }
        System.out.println("RET:" + ret);
        System.exit(ret == 0 || ret == -36 ? 0 : 1);
    }
}
