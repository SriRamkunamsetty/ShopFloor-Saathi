# 3-minute demo script (plain language)

Run locally with `uvicorn saathi.main:app`, open the app, and use the Role switcher.

1. **The problem (20 s).** "When a machine breaks, big factories have expensive systems. A small plant near
   Pune has a phone and a binder of manuals. Machine downtime is costly, and the people who know the fixes
   are retiring."
2. **The alert (20 s).** Point to the Machines panel: M-07 is red. "The system already noticed the cooling
   problem from the machine's readings."
3. **Ask in your own language (40 s).** In *Ask Saathi*, speak or type: **"M-07 garam ho raha hai"**.
   (With Gemini on, also attach a photo of the display.)
4. **See the reasoning (40 s).** Open *Diagnose* for M-07. Show the cause, the confidence, the exact numbers
   behind it, and the cited manual section. "It shows where the answer comes from. If it doesn't know, it says so."
   Open "How I got here" to show each step.
5. **A fix, not just advice (30 s).** Show the work order: steps, parts in stock, planned downtime. Switch the
   role to Supervisor and press **Approve**. "Nothing happens until a person approves."
6. **The numbers (30 s).** Show the top strip: rupees saved, minutes of downtime avoided, kg of CO₂ avoided.
   Open "Assumptions". "These are estimates, and you can change the assumptions to match your plant."
7. **Honesty test (20 s).** Ask about M-04 (healthy) with a made-up problem. It says it doesn't know instead of guessing.
8. **Close (10 s).** "Works today with a phone. Grows into Google's data platforms as the plant grows."

Say clearly: the data in this demo is simulated.
