import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.math.BigDecimal;
import java.math.RoundingMode;

public class BonusCalc_llm_a1 {
    private static final int DISPLAY_WIDTH = 10; // ZZZZZZ9.99 => 10 characters

    public static void main(String[] args) throws Exception {
        BufferedReader reader = new BufferedReader(new InputStreamReader(System.in));
        String inputLine = reader.readLine();
        if (inputLine == null) {
            return;
        }

        // Split by whitespace (space delimiter in COBOL)
        String[] parts = inputLine.trim().split("\\s+");
        if (parts.length < 2) {
            return;
        }

        String salaryStr = parts[0];
        String yearsStr = parts[1];

        // Convert salary string to numeric (NUMVAL equivalent)
        BigDecimal wsSalary = new BigDecimal(salaryStr);
        int wsYears = Integer.parseInt(yearsStr);

        // Determine bonus rate
        BigDecimal wsBonusRate = determineBonusRate(wsYears, wsSalary);

        // Compute bonus amount rounded to 2 decimal places
        BigDecimal wsBonusAmount = wsSalary.multiply(wsBonusRate)
                .setScale(2, RoundingMode.HALF_UP);

        // Compute total pay rounded to 2 decimal places
        BigDecimal wsTotalPay = wsSalary.add(wsBonusAmount)
                .setScale(2, RoundingMode.HALF_UP);

        // Prepare display strings according to PIC ZZZZZZ9.99
        String wsSalaryDisplay = formatDisplay(wsSalary);
        String wsBonusDisplay = formatDisplay(wsBonusAmount);
        String wsTotalDisplay = formatDisplay(wsTotalPay);

        // Output results (COBOL adds a space between literal and edited field)
        System.out.println("SALARY= " + wsSalaryDisplay);
        System.out.println("BONUS= " + wsBonusDisplay);
        System.out.println("TOTAL= " + wsTotalDisplay);
    }

    private static BigDecimal determineBonusRate(int years, BigDecimal salary) {
        if (years < 2) {
            return new BigDecimal("0.020");
        } else if (years < 5) {
            return new BigDecimal("0.050");
        } else if (years < 10) {
            return new BigDecimal("0.100");
        } else {
            if (salary.compareTo(new BigDecimal("1000000")) > 0) {
                return new BigDecimal("0.120");
            } else {
                return new BigDecimal("0.150");
            }
        }
    }

    private static String formatDisplay(BigDecimal amount) {
        // Ensure exactly two decimal places
        String plain = amount.setScale(2, RoundingMode.HALF_UP).toPlainString();
        // Pad on the left with spaces to reach the required width (10 characters)
        if (plain.length() >= DISPLAY_WIDTH) {
            return plain;
        }
        return String.format("%" + DISPLAY_WIDTH + "s", plain);
    }
}
