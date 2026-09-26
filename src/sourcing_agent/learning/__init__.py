"""Run-by-run learning for token minimization and cost optimization. Depends on core only; workers supply their own estimators.

runlog.py       every API pass recorded: shape, usage by meter, estimate, actual cost (the training data)
regression.py   estimated-vs-actual least-squares fit
calibration.py  corrects estimates with the fit; compares each new pass with its estimate
"""
