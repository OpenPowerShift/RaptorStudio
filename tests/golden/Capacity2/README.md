# Fixture: Capacity2 — manual Ch.14 capacity analysis variant

Same model/capture setup as `tests/golden/Capacity1` (see that README);
Capacity2 is the surge-capable 1500 W variant (nominal 500 W on all three
strings, max 650/900 W on the two larger ones; Capacity3 = Problem 11 base,
identical content to Capacity2).

Verification status mirrors Capacity1: capacity graph build confirmed
against `simDetails.TXT`; End_Of_Run ***CAPACITY*** sections and Final_
Results Units_Produced/Mean_Flow/Mean_Capacity rows ported and format-matched;
event-log ordering at multi-grant same-instants remains an open gap (see
Capacity1's README).
