"""Formation plans and the fast-loop controllers.

* ``directive``: the ``Directive`` a supervisor sets and the 50 Hz controller flies.
* ``formations``: formation shapes and the ``Path`` the formation centre follows.
* ``assignment``: Hungarian assignment of drones to slots.
* ``plan``: ``FormationPlan``, the virtual leader and every drone's slot.
* ``base``: ``Observation`` and the ``Controller`` interface.
* ``original``: the original controller, the baseline.
* ``improved``: the improved controller, with seven techniques that can be switched off.
* ``cbf``: the control-barrier-function safety filter used by the improved controller.
"""
