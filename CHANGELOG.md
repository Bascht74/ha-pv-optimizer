# Changelog

All notable changes to `PV-Ladesteuerung.yaml`. The version tag appears in
`blueprint.name` and in `bp_version`, and every log message carries it.

## V6.0.0

Added
- Discharge planning with a dynamic ToU minimum SOC. Each run derives the floor
  from the cumulative net balance of up to three forecast days (Solcast tomorrow,
  day 3, day 4 as new optional inputs; PV damped per day and reduced by charging
  losses, consumption from the learned profile): the battery is discharged at
  night only as far as the coming days refill it to the target, and never held
  so high that a sunny day would export. The reserve gives way only when keeping
  it would force export. New inputs: reserve SOC, target SOC, confidence for day
  3 and 4, P50 weight. Without the tomorrow forecast assigned, nothing changes.
- The floor is written only when it can act: a change of at least 5 points and
  the SOC within 10 points of the higher of old and new floor, or a hold.
- While the cell balancing is overdue the planning target becomes 100 %, so the
  full charge lands on the last day of a sunny stretch and never comes from the
  grid.

Removed
- The winter forced grid charge, whole: its three triggers, the priority branch
  that held the charge current, the midnight safety resets of the grid-charge
  switch, and the inputs for the grid-charge switch, the grid-charge current, the
  forced-charge current, the "forced charge active" helper and the cooldown
  duration. Cell balancing is reached through PV only; the discharge planning
  raises its target to 100 % when the balancing is overdue. The remaining
  priorities are renumbered 0 to 7 without a gap.
- The escalation minimum SOC input and the two rules that set the ToU programs
  after two full days or nine days without a full charge.
- Instances that still assign any removed input must drop those lines before
  the blueprint loads; the cooldown timer input stays and is now named
  "Zellausgleich-Nachlauf".

Changed
- The stored-loss booking compares the night's low against the current ToU
  floor instead of the fixed minimum.
- The sunset warning about a battery that did not fill is suppressed while the
  planning target is below 100 %; staying below it is then intended.

## V5.10.3

Fixed
- The heat-pump entity fields can be left empty on a site without a heat pump.
  They were inserted directly into action targets, and Home Assistant refuses to
  load an automation with an empty target ("expected 'all' or 'none'"), so the
  fields had to hold placeholder entities. They are now templated and guarded,
  and the mandatory-field check demands them only while the boost is enabled.
  The logbook anchor and the grid-charge current join the mandatory-field list,
  as both are used directly.

## V5.10.2

Fixed
- The diagnostic recording writes again. The Solcast forecast attribute carries
  its slot starts as datetime objects, which the template JSON encoder rejects;
  the whole line was lost with a render error. Slot starts are now written as
  ISO strings, and the test harness rejects datetime objects the same way Home
  Assistant does, so the case is covered.

## V5.10.1

Fixed
- The half-hour consumption slot is derived from the trigger time, not from the
  clock at execution. The queued automation can delay the half-hour run by
  minutes; from 15 minutes on, the measured energy landed in the following slot
  of the consumption profile that the morning blockade and Prio 8 rely on. The
  midnight detection of that run uses the same basis.
- The fallback for an unreadable charge-current register is the configured
  maximum current instead of a fixed 140 A, so an unknown reading cannot trigger
  a raise. The remaining fallbacks on computed current values could never apply
  and are removed; each current quantity now has a single default.

## V5.10.0

Added
- Optional diagnostic recording. When an entity named
  `notify.pv_optimizer_aufzeichnung` exists (File integration), every half-hour run
  appends one JSON line with all input values: configuration, entity states with
  `last_changed`, and the attributes the blueprint reads, above all the Solcast
  forecast this run actually saw. Solcast reshapes the curve during the day, so a
  later export cannot reproduce a decision. Without the entity nothing happens;
  no new input field. The test harness rebuilds a run from such a line.

Fixed
- The daily housekeeping no longer depends on the inverter answering. The Modbus
  retry ends the run when the connection stays down, and the helper resets came
  after it: a midnight outage left "full today" and the blockade marker set for
  the whole next day, locking out top balancing, peak shaving and the morning
  blockade. The resets and the 100 %-day list now run before the retry; only the
  checks that read inverter registers stay behind it.
- Battery capacity derives from the configured packs, not from the packs whose
  BMS happens to report. A dropout of seconds halved the energy model for that
  run, and the Prio 8 hysteresis then held the current set from it.
- The hot-water boost cannot start on a dead temperature sensor. An unavailable
  reading fell back to 50 degrees, which reads as "cold" and satisfied the start
  condition while never satisfying "target reached".
- The mandatory-field check runs right after the input variables, before the
  first block that reads a measurement. It sat behind the housekeeping blocks,
  which had already worked with substitute values by the time it stopped the run.

Changed
- The automation queue holds 20 runs instead of 10. With 21 triggers, several of
  which fire in bursts, and single runs that can take up to 50 s through the
  restart grace period, the Modbus retry and the heat-pump read-back loops, ten
  slots filled up and everything beyond was dropped without a trace in the log.

## V5.9.0

Fixed
- The reality check compares like with like across the running half-hour slot. The
  daily counter already includes the elapsed part of the current slot while the
  forecast sum leaves it out, so the denominator was too small and the ratio too
  optimistic within every half hour - the direction that costs charge, since an
  overstated surplus delays escalation.
- The hot-water boost waits out a grace period after a watchdog end. The end condition
  (export above the peak threshold) necessarily satisfies the start condition at the
  lower boost threshold while the water is still cold, so the boost restarted in the
  same second and a fresh startup lock held the battery at 0 A. The grace period
  derives from the configured startup duration and needs no new input.
- Prio 6 no longer overwrites the stored mode while it reads "blockade". Exit detection
  keys on that mode, and in a scheduled run the peak hold timer starts only after it,
  so an overwrite loses the exit until the midnight reset. The guard sits around the
  mode write alone; the capping itself is untouched.
- Two peak-shaving entries named an action their branch never performs and a reason it
  never evaluates. Both now state what the branch does and which condition applied.
- The morning blockade and Fall B print their derivation with the same precision as
  the decision line, which switches to two decimals at a narrow margin. Cut to one
  decimal, the printed calculation could reverse the comparison it was meant to show.

Added
- Prio 8 and Fall B report the running half-hour's house consumption against the
  learned profile when it lies well above it. The reality check covers the PV side
  only, so an unplanned large load ate the same surplus without appearing in the log.

Changed
- The morning blockade applies the forecast haircut to the blend rather than to the
  surplus, so house load is no longer discounted along with it. The latest start came
  out too high by house load times one minus the haircut - the same order as the margin
  the branch decides on, since it always picks the tightest workable start.

## V5.8.2

Changed
- blend_p50_anteil back to 0.5, weighting P50 and P10 equally again. With the
  quadratic discount removed the weighting carries the forecast uncertainty alone, and
  at 0.333 it plans so far below the yield a wide P10/P50 band delivers that Fall B
  escalates while the remaining window still covers the demand.

## V5.8.1

Fixed
- The safety-cap branch no longer reacts to a momentary sensor dropout: packs_online
  carries no debounce, so a single missing reading pulls the temperature limit down to
  the emergency current, which the branch writes straight to the register. It now acts
  only when the limit rests on a measured temperature or the outage has been confirmed
  over the debounce period, and does nothing in the uncertain state between the two.
- Fall B restores the charge current when something throttled it mid-day. While the
  mode is fall_b_max, Prio 8 stops without regulating and the entry block writes only
  on the mode change, so a current pulled down by the safety cap stayed there until
  sunset. Both sides aim at the capped maximum, so an active limit cannot start a
  tug-of-war between them.
- A missing space in the Fall B log entry ran two words together.

## V5.8.0

Changed
- The forecast blend is weighted by the internal parameter blend_p50_anteil (0.333 by
  default) instead of averaging P50 and P10 equally, and the separate uncertainty
  discount on the PV side is gone: it was blend/P50, applied to the very blend it was
  derived from, so the P10 gap counted twice and entered the plan quadratically.
- The blockade's demand factor drops prognose_unsicherheit for the same reason. With
  the weighting already carrying the uncertainty on the supply side, a second factor on
  the demand side tightens both at once. prognose_unsicherheit remains only as a
  diagnostic figure in the log, and its value depends on the chosen weighting.
- The charging window's cut-off scales with the discount. It compared discounted slots
  against an undiscounted peak, so the window length hung on how much had been
  discounted rather than on the shape of the forecast alone.
- The trend note always states the weighting. Without the uncertainty discount the
  reality check leaves the forecast untouched on most days, and no entry would
  otherwise say what the expected yield rests on.

## V5.7.2

Fixed
- Fall B's decision line names the right quantity when the charging lead shrinks to
  zero. With a lead of zero hours the two figures are separated by the charging window
  alone, so the entry names the window and the actual reason: the late slots deliver
  too little to sustain the charge current.

## V5.7.1

Fixed
- The safety-cap branch no longer fires at night. The setpoint sits at maximum then by
  design and no charge current flows, so the cap protects nothing while overwriting the
  night value and filling the log whenever the BMS drops out after sunset. It engages
  again at sunrise, well before any meaningful yield.

Changed
- The decision line of the blockade and Fall B entries switches to two decimals
  when the two figures lie less than 0.1 kWh apart. Rounded to one, a genuine
  difference printed as the same number on both sides of the operator and read
  as a contradiction; rounding one side up and the other down would have hidden
  the same problem behind a gap wider than the real one.

## V5.7.0

Fixed
- The mode bypass in Prio 8 no longer overrides the no-lowering threshold. The
  bypass exists so a single pass can regulate through after another branch has
  written, but it released lowering as well, so every peak-shaving episode ended
  with the current pulled back down on a nearly full battery, delaying the very
  top-off the threshold protects. Raising still bypasses freely, and below the
  threshold nothing changes.
- The morning blockade yields to the export trigger only when peak shaving can
  actually start. During the heat-pump start lockout the hold timer is cancelled
  rather than started, so the blockade gave up its daily marker for a branch
  that never ran, and the boost and peak thresholds sit close enough together to
  coincide regularly. Once the timer really runs, the blockade steps aside as
  before.

Changed
- The blockade log entry closes each calculation with its result, omits the
  factor cap and the top-up term where they do not apply, marks the named time
  when it sits at the configured upper limit, and states the surplus expected
  before charging begins on the same basis as the figure it is compared with.
  The previous total was uncorrected while the figure beside it was not, so the
  difference between them invited a wrong reading.

## V5.6.1

Fixed
- Prio 8 no longer writes the register when the target already matches the current
  setpoint. The mode bypass releases the hysteresis whenever another branch wrote
  last, but it never asked whether anything actually changes, so the result was a
  no-op write plus an entry announcing an adjustment of zero amps. Every other
  regulating branch already guards its write this way.
- The charge mode is now cleared outside the write branch, so it still follows when
  there was nothing to write and its bypass does not stay armed run after run.
- The hot-water boost end no longer reports the runtime timer as expired. The export
  fallback stops the runtime early and the branch runs in that case as well, where
  that reason was simply wrong.

## V5.6.0

Fixed
- The BMS-offline warning and logbook message no longer claim charging continues
  below the cap when the current actually sits above it.

Added
- A safety-cap branch heads the cascade and returns the charge current to the active
  safety limit whenever the setpoint sits above it. The limit only capped the targets
  the other branches compute, so it took effect solely when one of them wrote anyway:
  above the no-lowering threshold Prio 8 stops regulating, and the lowering hysteresis
  needs a 20 A gap, so a smaller overhang stayed in place indefinitely with cell
  voltages and temperatures unmonitored.

Changed
- Fall B writes the capped maximum instead of the raw one. It holds its value until
  sunset, so with the BMS offline it would have charged at full current for the rest
  of the day, and it would have fought the new branch for the register on every
  five-minute cycle.

## V5.5.1

Fixed
- The heat-pump lockout branches no longer overwrite the charge mode while it reads
  "blockade". Exit detection for the morning blockade keys on the stored mode, so any
  branch that overwrites it mid-blockade leaves the exit unregistered: the daily marker
  stays unset and the end is never logged. It also stripped the blockade of the
  "already running" state that shields it from the two-hour minimum. Peak shaving is
  unaffected because it clears the blockade in the same run, before the cascade writes.

## V5.5.0

Changed
- Prio 8 raises the setpoint by 10 A immediately, or by 5 A after an hour without a
  write. This reverts the immediate 5 A rule: with the charging window narrowing
  through the afternoon, the target climbs fast enough to clear the 10 A threshold on
  its own, so the rule only added register writes without filling the battery any
  sooner. The problem it originally addressed no longer occurs once the window itself
  narrows.
- The morning-blockade message calls its figure the expected start of charging rather
  than the latest one. It is recalculated on every run, so a time announced in the
  morning can still move, and "latest" read as a commitment that was then not kept. The
  input keeps its name, since that one really is a hard limit.

## V5.4.0

Fixed
- The Fall B message quoted the surplus of the entire remaining window while the
  decision was made on the shortened one, which read like a contradiction:
  available above needed, yet escalating. It now states the figures the decision
  rests on, and adds what would be available through to the end of the day.
- Durations appeared as raw hour values in log messages instead of readable ones.

Changed
- Fall B now checks against a shortened charging lead, half an hour less than
  Prio 8 plans with. Without that gap it fires every afternoon: the moment Prio 8
  can no longer meet the lead, the Fall B condition is met as well, even though
  the full window still holds enough energy. "The energy does not suffice at all"
  and "only the lead is missed" are two different situations and no longer get
  the same answer.
- The gap sits on the Fall B side rather than as a surcharge on Prio 8, so the
  charge current stays as it is and with it the headroom kept for peak-shaving.
  Putting it on the Prio 8 side would charge faster than needed on days where
  output continues well into the evening.

## V5.3.0

Fixed
- Peak shaving no longer undercuts the Prio 8 charge target. It sits ahead of Prio 8 in
  the cascade and derived its value from the step logic alone, so it could set its own
  step even when Prio 8 wanted a higher current in the same run. The step logic is
  unchanged, it just cannot pull the value down any more - both branches want surplus
  in the battery, and the higher value serves both.
- The heat-pump watchdog waits two minutes after the last timer change. The runtime
  branch sets the target back immediately, but the heat pump adopts it with a delay, so
  the watchdog read the old value in the same run and reported a safety reset that had
  not happened. For its actual purpose - a value still wrong hours later, or a timer
  lost across a restart - the delay is irrelevant.

Changed
- The hot-water boost only blocks Prio 8 while enough charging time remains afterwards.
  A long boost consumed the entire charging lead and left the battery idle through the
  best part of the afternoon. If the boost outlasts the time available beyond the
  charging lead, Prio 8 keeps regulating and heat pump and battery share the surplus.
- The morning blockade applies a haircut to the forecast, as Prio 8 does. Until the
  reality check has a usable value, the inverse of the escalation factor (1/1.2) stands
  in for it; once the check applies, the measured figure takes over. The blockade
  previously planned with raw forecast values while the afternoon regulation worked
  with reduced ones, so it held on to output that was never going to arrive. The Prio 7
  log message names the applied haircut and where it comes from.

## V5.2.0

Changed
- The morning blockade now applies the same charging window and charging lead as
  Prio 8. It summed surplus through to the last slot of the day and granted itself the
  full day, so dusk slots that cannot sustain the charge current counted as available
  and the latest start time sat too late.

## V5.1.1

Fixed
- The mandatory-field check reported the half-hourly house-consumption input as
  unassigned on every run. The variable was only defined inside the JSON-profile
  block and undefined everywhere else; it is now assigned with the other inputs, so
  the check sees the value that was there all along.

Changed
- The mandatory-field check now sits directly before the priority cascade instead of
  aborting the run ahead of the midnight maintenance, the loss tracking and the
  watchdog branches. A missing field stops the charge-current control, which is the
  part that would otherwise regulate on substitute values, and leaves the
  housekeeping intact.

## V5.1.0

Changed
- The charging window now ends where forecast output drops below 15 % of the day's
  peak instead of running to the last slot with any output at all. Dusk slots cover
  demand on paper while almost nothing reaches the battery, which held the charge
  current down all afternoon. The reference is the whole day including elapsed slots:
  against the strongest remaining slot the threshold would move once the midday peak
  has passed, and the window would grow again in the evening.
- Prio 8 raises the charge current as soon as the target is 5 A above the setpoint,
  without the previous one-hour wait. The target grows slowly over the afternoon, so
  the wait often meant it never took effect, and charging too little costs energy
  that is missing later. Lowering stays deliberately slow and still only happens
  below the no-lowering threshold.
- The current search runs in 1 A steps over the whole range rather than a coarse 5 A
  pass followed by a fine one, and evaluates the window both with and without the
  charging lead, taking the higher result. Switching between the two would not be
  monotonic - more demand could then yield less current.
- Fall B also triggers when demand cannot be met within the charging lead, not only
  when it cannot be met at all. A high setpoint costs nothing there because PV limits
  the charge anyway, and it fills the battery as early as possible instead of
  dropping the lead the moment it gets tight.

## V5.0.0

Fixed
- Section headings rendered as "1." throughout: Home Assistant renders blueprint names
  as Markdown, and both "1." and "1)" open an ordered list, each section being its own
  block. They now use a separator Markdown ignores.
- The Deye availability check no longer reports an unassigned entity field as an outage,
  which would mask the actual cause.

Added
- New input "PV-Erzeugung heute (kWh)" feeding a reality check on the forecast: measured
  daily yield is compared against the forecast blend for the elapsed part of the day, and
  the ratio lowers the expected yield of the remaining slots. It applies from the midpoint
  of the charging window onwards; earlier in the day a single gap in the clouds dominates
  the ratio.
- The charging window spans the slots where forecast PV exceeds house load, so both ends
  are cut on physical grounds rather than by an arbitrary threshold.
- A startup check names unassigned mandatory entity fields and stops the run instead of
  regulating on substitute values.

Changed
- Forecast haircuts now reduce expected PV yield per slot instead of multiplying the
  simulated charge current. Forecast uncertainty and reality check express the same thing
  - yield will fall short of the planned blend - so the larger haircut wins rather than
  both stacking. A multiplier on the ampere value also raised it in slots where PV, not
  the charge current, was the limit, and had no effect there.
- Prio 8 gains a lead time in place of its safety factor: the simulation runs over the
  remaining slots truncated by that lead, so the required current follows from it instead
  of being estimated. A fixed factor cannot express a fixed lead time - the same lead
  needs a different factor depending on demand and slot shape.
- If the truncated window cannot cover the demand, the full window is used, so the lead
  never lowers the current.
- Safety factor and inverter offset now add up instead of competing via max(), which
  swallowed the factor entirely at low currents. The offset only compensates the inverter
  regulating below its setpoint and is not a reserve.
- The morning blockade no longer multiplies in that factor, so a larger charging lead no
  longer shortens the peak-shaving window.
- Durations are entered as HH:MM:SS like the other duration fields rather than as a
  decimal number of hours.

Removed
- Input "Sicherheitsfaktor für Ungeplantes", replaced by "Ladevorlauf". Together with the
  new mandatory PV yield input, existing instances need reconfiguration.
