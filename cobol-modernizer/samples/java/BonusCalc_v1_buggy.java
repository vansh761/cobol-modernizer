import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Scanner;

/**
 * Migrated from BonusCalc.cbl.
 *
 * NOTE: this version has an intentionally introduced bug, kept in the repo
 * on purpose as the M1 proof case: it compiles cleanly (javac gives no
 * errors) and passes 4 of 5 golden cases, but silently produces the wrong
 * answer on the "senior, normal salary" edge case. This is exactly the bug
 * class that AST/compile validation alone CANNOT catch - only the
 * behavioral diff against real COBOL output catches it. See
 * BonusCalc_v2_fixed.java for the corrected version.
 */
public class BonusCalc_v1_buggy {

    public static void main(String[] args) {
        Scanner scanner = new Scanner(System.in);
        String line = scanner.nextLine().trim();
        String[] parts = line.split("\\s+");

        BigDecimal salary = new BigDecimal(parts[0]);
        int years = Integer.parseInt(parts[1]);

        BigDecimal bonusRate = determineBonusRate(salary, years);
        BigDecimal bonusAmount = salary.multiply(bonusRate).setScale(2, RoundingMode.HALF_UP);
        BigDecimal totalPay = salary.add(bonusAmount).setScale(2, RoundingMode.HALF_UP);

        System.out.println("SALARY=" + formatLikeCobol(salary));
        System.out.println("BONUS=" + formatLikeCobol(bonusAmount));
        System.out.println("TOTAL=" + formatLikeCobol(totalPay));
    }

    private static BigDecimal determineBonusRate(BigDecimal salary, int years) {
        if (years < 2) {
            return new BigDecimal("0.020");
        } else if (years < 5) {
            return new BigDecimal("0.050");
        } else if (years < 10) {
            return new BigDecimal("0.100");
        } else {
            // BUG: the COBOL source's two branches got swapped during
            // translation - a very plausible LLM mistake since both
            // branches are single MOVE statements that look superficially
            // similar, and nothing about Java's syntax flags the swap as
            // wrong. It compiles perfectly and reads as "reasonable" code.
            // It will silently pay every 10+ year employee under 1,000,000
            // salary the WRONG bonus rate (0.12 instead of 0.15) - a real
            // payroll-correctness bug that only a behavioral diff against
            // actual COBOL output catches.
            if (salary.compareTo(new BigDecimal("1000000")) > 0) {
                return new BigDecimal("0.150");
            } else {
                return new BigDecimal("0.120");
            }
        }
    }

    /** Right-pads/formats to loosely resemble COBOL's ZZZZZZ9.99 PIC display. */
    private static String formatLikeCobol(BigDecimal value) {
        BigDecimal scaled = value.setScale(2, RoundingMode.HALF_UP);
        String formatted = scaled.toPlainString();
        int width = 10; // "ZZZZZZ9.99" = 6 Z's + 1 digit + '.' + 2 digits = 10 chars
        while (formatted.length() < width) {
            formatted = " " + formatted;
        }
        return formatted;
    }
}
