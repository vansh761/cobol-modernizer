      *****************************************************************
      * BONUS-CALC
      * Legacy payroll bonus calculator. Reads a salary and years of
      * service from the command line (via ACCEPT), applies tiered
      * bonus logic, and prints the result. This is representative of
      * the kind of small COBOL business-logic units enterprise
      * modernization projects actually migrate: not toy code, but not
      * a whole mainframe system either.
      *****************************************************************
       IDENTIFICATION DIVISION.
       PROGRAM-ID. BONUS-CALC.

       ENVIRONMENT DIVISION.

       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-SALARY            PIC 9(7)V99.
       01 WS-YEARS              PIC 9(2).
       01 WS-BONUS-RATE         PIC V999.
       01 WS-BONUS-AMOUNT       PIC 9(7)V99.
       01 WS-TOTAL-PAY          PIC 9(7)V99.
       01 WS-SALARY-DISPLAY     PIC ZZZZZZ9.99.
       01 WS-BONUS-DISPLAY      PIC ZZZZZZ9.99.
       01 WS-TOTAL-DISPLAY      PIC ZZZZZZ9.99.
       01 WS-INPUT-LINE         PIC X(20).

       PROCEDURE DIVISION.
       MAIN-LOGIC.
           ACCEPT WS-INPUT-LINE FROM CONSOLE
           UNSTRING WS-INPUT-LINE DELIMITED BY SPACE
               INTO WS-SALARY-DISPLAY WS-YEARS
           MOVE FUNCTION NUMVAL(WS-SALARY-DISPLAY) TO WS-SALARY

           PERFORM DETERMINE-BONUS-RATE
           COMPUTE WS-BONUS-AMOUNT ROUNDED = WS-SALARY * WS-BONUS-RATE
           COMPUTE WS-TOTAL-PAY ROUNDED = WS-SALARY + WS-BONUS-AMOUNT

           MOVE WS-SALARY TO WS-SALARY-DISPLAY
           MOVE WS-BONUS-AMOUNT TO WS-BONUS-DISPLAY
           MOVE WS-TOTAL-PAY TO WS-TOTAL-DISPLAY

           DISPLAY "SALARY=" WS-SALARY-DISPLAY
           DISPLAY "BONUS=" WS-BONUS-DISPLAY
           DISPLAY "TOTAL=" WS-TOTAL-DISPLAY

           STOP RUN.

       DETERMINE-BONUS-RATE.
      *    Tiered bonus logic - the actual "business logic" that must
      *    survive migration unchanged. This kind of nested condition
      *    is exactly where an LLM can silently drop an edge case.
           IF WS-YEARS < 2
               MOVE 0.020 TO WS-BONUS-RATE
           ELSE
               IF WS-YEARS < 5
                   MOVE 0.050 TO WS-BONUS-RATE
               ELSE
                   IF WS-YEARS < 10
                       MOVE 0.100 TO WS-BONUS-RATE
                   ELSE
                       IF WS-SALARY > 1000000
                           MOVE 0.120 TO WS-BONUS-RATE
                       ELSE
                           MOVE 0.150 TO WS-BONUS-RATE
                       END-IF
                   END-IF
               END-IF
           END-IF.
