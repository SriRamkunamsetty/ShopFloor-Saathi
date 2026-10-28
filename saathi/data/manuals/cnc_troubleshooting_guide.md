# CNC Machining Centre Troubleshooting Guide (demo manual)

This is an original, synthetic manual written for the ShopFloor Saathi demo. It is not a real vendor document. Replace it with the plant's own manuals.

## Safety before any repair
Apply lockout/tagout (LOTO) before opening guards, touching the spindle, tool holder, fan or drive cabinet. Isolate power, wait for the spindle to stop completely, and verify zero energy. Only trained maintenance staff may open the electrical cabinet. Never bypass door interlocks. If coolant or hydraulic oil has leaked on the floor, clean it before starting work.

## Heat dissipation failure (HDF)
Symptoms: machine feels hot, process temperature is only a few kelvin above air temperature, spindle speed sags below normal, thermal alarm or overheating message on the HMI, hot smell near the cabinet. In hot weather the air temperature rises and the cooling margin shrinks.
Checks: cooling fan running and not blocked, filter or radiator clogged with dust or chips, coolant level and flow, coolant pump seal leaking, cabinet vents covered.
Corrective actions:
1. Stop the cycle at a safe point and apply LOTO.
2. Inspect the cooling fan: spin by hand, check for seized bearing, broken blades or loose coupling.
3. Clean the radiator, cabinet filter and vents with compressed air from the inside out.
4. Check coolant level and pump flow; replace the pump seal if there is a leak.
5. Restart at reduced spindle speed and watch the temperature gap for 10 minutes before returning to full production.
Escalate to the maintenance supervisor if the temperature gap does not recover after cleaning.

## Tool wear failure (TWF)
Symptoms: rising cutting noise, poor surface finish, burrs, growing dimensional drift, tool wear counter near or above 200 minutes, higher torque than normal for the same cut.
Checks: insert edge condition under a magnifier, tool life counter, correct feed and speed for the material.
Corrective actions:
1. Stop at the end of the current part and apply LOTO.
2. Replace the carbide insert or tool and torque the screw to specification.
3. Reset the tool life counter and run a first-off inspection before full production.
4. Record the actual tool life in the maintenance log to tune the replacement interval.

## Power and drive failure (PWF)
Symptoms: sudden load spikes, drive overload alarm, tripped spindle drive, power draw far above or below the usual range for the current speed, burning smell from the drive cabinet.
Checks: spindle drive fuse, power module and its cooling, loose power terminals, crashed or jammed tool, wrong cutting parameters.
Corrective actions:
1. Stop the machine immediately and apply LOTO. Do not restart after a drive trip without a check.
2. Inspect for a jammed tool or workpiece and clear it.
3. Have a qualified electrician check the spindle drive fuse, terminals and power module.
4. Replace faulty fuse or module only with the specified rating.
5. Test with a no-load run before cutting.
Escalate to the maintenance supervisor on any repeated trip.

## Overstrain failure (OSF)
Symptoms: high torque on a worn tool, chatter, tool holder slipping, spindle load alarm, product type with low strength limit. The product of tool wear and torque exceeds the limit for the quality variant.
Checks: tool wear counter, clamping force, tool holder taper condition, depth of cut and feed.
Corrective actions:
1. Reduce depth of cut and feed, or stop the cycle, and apply LOTO.
2. Replace the worn tool and inspect the tool holder taper and clamp set.
3. Re-check the clamping force and workpiece fixturing.
4. Resume with reduced parameters and watch spindle load.

## Bearing and spindle noise
Symptoms: grinding, humming or squealing noise from the spindle, vibration at certain speeds, rising bearing temperature.
Checks: lubrication level and schedule, spindle runout, belt tension, coolant ingress into the bearing.
Corrective actions:
1. Stop and apply LOTO.
2. Check lubrication and re-grease to the specified quantity only; over-greasing also heats the bearing.
3. Check belt tension and pulley alignment.
4. Plan a spindle bearing replacement if noise remains after lubrication.

## Coolant system faults
Symptoms: low coolant flow, foaming, bad smell, tool burn marks, temperature rising during cuts.
Checks: coolant concentration, filter, nozzle blockage, pump motor, tank level.
Corrective actions:
1. Top up and correct concentration.
2. Clean the filter and nozzles.
3. Check pump rotation and flow.
4. Replace pump seal if leaking.
