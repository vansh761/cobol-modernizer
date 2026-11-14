import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Scanner;

/**
 * Migrated from BonusCalc.cbl. This is the M1 "attempt 2" - the branch-swap
 * bug from BonusCalc_v1_buggy.java is fixed here. In the real pipeline
 * (M4), this fix is what the self-correction loop's LLM call would produce
 * automatically after being fed v1's diff failure; here it's applied by
 * hand to prove the validate→diff→fix→revalidate cycle end to end before
 * wiring an actual LLM into that loop.
 */
public class BonusCalc_v2_fixed {

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
            // FIXED: matches COBOL source exactly -
            //   IF WS-SALARY > 1000000  -> 0.120
            //   ELSE                    -> 0.150
            if (salary.compareTo(new BigDecimal("1000000")) > 0) {
                return new BigDecimal("0.120");
            } else {
                return new BigDecimal("0.150");
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
