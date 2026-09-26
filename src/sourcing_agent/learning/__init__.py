"""Run-by-run learning for token minimization and cost optimization. Depends on core only; workers supply their own estimators.

runlog.py       every API pass and its conversations: settings, shape, usage by meter, outcomes, estimate, actual cost (training data)
profile.py      token profile learned from logged conversations (replaces guessed constants once there is data)
regression.py   estimated-vs-actual least-squares fit
calibration.py  corrects estimates with the fit; compares each new pass with its estimate
report.py       spend by settings, meter, category, and conversation; recommendations
commands.py     `sourcing learn`
"""
