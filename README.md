# lab-tycoon-desktop
Demo LIS for education

### Live floor simulation

The floor plan's Play button runs the selected workflow and scenario through the
SimPy engine. Set the specimen/order count, optional random seed, and speed
(simulated hours per real second). Equipment animates while its node is busy;
queue, active, and completed counts come from the running engine. Tube and report
markers follow actual specimen transitions, with brief visual interpolation that
does not add transport time to the simulation.

Pause freezes both the engine clock and animation. Play resumes the same run.
Stop cancels an unfinished run. Closing the floor window leaves its project-owned
session running; reopening reconnects to it. Closing or replacing the project
cancels its unfinished session. Sessions are in-memory and do not resume after
restarting the application.

Completed runs save their generated simulation patients and orders separately
from LIS records. Orders are generated when the workflow uses a simulation Order
Adapter; legacy workflows can still process synthetic specimens without order
records. Equipment processing rates of zero complete instantly, so configure
positive rates to see activity over time. Reporting and Validating remain
processing steps; the terminal Reporting Station adds no processing delay.

### Staff attendance on the floor

Staff appear only during the human-processing phase, after the node acquires its
required staff from the shared pool. Waiting for staff does not display an
attendant. A randomly selected male or female sprite represents the allocated
staff at each equipment, with the total allocated count above its head. Pause
freezes the pose; release or cancellation removes the attendant.

Equipment nodes use their own floor location. Process-node properties include
**Attend at** to select a placed equipment instance. With **Automatic** selected,
Reporting and Validating use the Reporting Station when exactly one is placed;
Specimens Processing uses the centrifuge when exactly one is placed. Choose an
explicit station when multiple matching stations exist. Required staff and a
positive Human Processing Capacity must both be configured for human work.
Reception and reporting station artwork no longer includes permanent staff.

Staff poses follow the node's process: registration scans a tube, specimen
processing handles tubes, reporting types, and validating reviews a clipboard.
Each activity has male and female sprite variants. Concurrent Reporting and
Validating allocations at one station display separate attendants and count
badges. Select the appropriate node type in workflow properties; older generic
nodes named Reporting or Validating are also recognized.

Equipment nodes referencing the same physical equipment ID share one SimPy
resource within a run. Their requests queue against the shared capacity, and all
references must specify the same Capacity. Different equipment IDs and ordinary
process nodes retain independent resources. **Attend at** only assigns a staff
location; it does not reserve the equipment. Terminal Reporting Stations bypass
resource acquisition and add no processing time. Separate simulation runs own
separate resource pools.


Shared equipment routing: equivalent direct equipment alternatives prefer the
lowest occupied/queued load per capacity slot, then the fewest prior assignments
on the physical equipment, then estimated processing duration. This shares work
between compatible idle machines even when orders arrive slowly or machines
have different speeds. Final ties follow connection order.
The estimate includes active and queued requests on the physical resource; it
does not predict remaining job time or staff availability. Explicitly supplied
routers retain control of routing.

For parallel testing, connect each centrifuge to the analyzer alternatives and
connect every analyzer directly to the same reporting process or Reporting
Station. Use automated/manual analyzer node types and configure their selected
tests and positive processing capacities (Human Processing Capacity for manual
analyzers). An empty test menu retains the existing meaning of accepting any
test. The engine assigns each pending test code once to a compatible enabled
analyzer, runs the assigned groups concurrently, then joins their results before
continuing the order. Analyzer alternatives may have different test menus.
Sequential analyzer chains continue to process remaining tests in order.

Orders use the existing one-specimen-per-order model. Test branches share the
order identity and history, but maintain independent pending/completed test
lists; they do not create extra orders or completion notifications. Reporting,
validating, terminal Reporting Stations, and normal workflow completion require
all required tests to be complete. Unsupported tests, disabled required
analyzers, or zero processing capacity stop the run with an order/test-specific
error rather than count the order as reported. Floor transfer events retain the
centrifuge source for parallel analyzer arrivals and both analyzer sources at
the reporting station. Arbitrary divergent subgraphs are not inferred as
parallel test pools; analyzer alternatives must share one direct successor.

Generated simulation orders request the union of selected tests on reachable,
enabled analyzers. If analyzer menus are unrestricted, they request one explicit
`SIM-TEST` instead. This gives generated orders real analyzer work and exercises
the same result-completion gate as imported orders. Imported test requests are
not modified. Work sharing still respects test compatibility and enabled state;
a machine that cannot perform any requested tests will remain idle.


Staff batching in Specimens Processing: generated orders already permit grouped
arrivals. By default all orders are created at simulated time zero; scenario
`interarrival_time` (in simulated hours) can space them closely or farther apart.
Arrival generation remains independent of staff collection and preparation.

Explicit Specimens Processing task nodes with positive Required staff and Human
Processing Capacity collect up to 5 specimens, waiting at most 1 simulated minute
from the oldest queued arrival. Edit **Maximum specimens per batch** and
**Maximum collection wait (minutes)** in Node Properties; a batch size of 1
restores individual processing. Batch settings are saved in the project.
A full batch starts without waiting for the collection deadline; a partial batch
is released at the deadline and then waits for node capacity and staff.

One staff allocation serves the group, with specimens processed sequentially at
the configured per-item machine and human rates. There is no assumed batching
speed discount. Staff remain allocated throughout that preparation batch, and
the prepared group moves downstream together. Node Capacity limits concurrent
batches; available staff can impose a smaller limit. Analyzer tests and reporting
retain their independent per-order completion rules. Histories record batch
membership, preparation finish and group release; wait statistics include the
collection delay, staff/resource queues and waiting for the rest of the batch.
