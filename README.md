# lab-tycoon-desktop
Demo LIS for education

### First-run administrator setup

When the users table is empty, startup opens **Set Up Administrator**. Enter a
first name, last name, username, password, and matching password confirmation.
Email, position, and license ID are optional. The account is active and receives
all roles, including admin, approver, and reporter. Sign in with the account you
created. Closing setup exits the app; setup appears again on the next launch.
Existing accounts are preserved, and no default Jane account is created.

### Try the demo laboratory

Choose **Open Demo Project**, enter a project name, and select an existing Lab
Tycoon database or create a new SQLite database file. The current database is
preselected. Database selection applies to the whole application. If you change
it, the app closes after saving the selection; reopen it to continue with your
named demo. A new database includes sample patients, doctors, and tests and asks
you to create an administrator account on startup. Existing database files are
never overwritten by Create New.

Then choose **Go to Lab** and **Play**. The demo includes
reception, a centrifuge, one chemistry analyzer performing TG tests, a reporting and validation workflow ending at the reporting station, and
one simulated staff member named Alex. The default run processes 10 orders arriving one simulated minute apart.
This runs the simulation without adding LIS patient or order records. Save the
project to keep the example or your edits.

### Creating a project

**File → New Project** accepts a project name, description, and database choice.
Keep the preselected current database, browse to another existing Lab Tycoon
database, or choose **Create new database** and a new file location. A database
change applies across the app and requires reopening it. The named blank project
and description are restored after restart, following administrator setup when
needed. Cancelling the dialog does not change database settings.

### Main laboratory workspace

An active project is required to use LIS, simulation, and tool views. On the
home screen, choose **New Project**, **Open Project**, or **Open Demo Project**.
The prominent **Active project** heading identifies the current project even
when the Simulation menu is hidden. Closing the project disables workspace
actions until another project is opened. Account sign-in and program settings
remain available. LIS records still belong to the application's selected database;
opening a project does not isolate or switch those records.

**Go to Lab** opens the floor plan as the main laboratory workspace. Use its
**Edit Workflow**, **Orders**, **Customers**, and **Results** buttons to open
secondary views; records open on the requested tab. **Save Project** saves the
active project, and **Back to Home** restores the home screen. The project-owned
simulation remains available when moving between these views.

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
Station. Equipment nodes linked to a placed chemistry analyzer automatically use
analyzer processing. Open that Equipment node’s configuration and use **Select tests** to configure
its test menu and a positive Processing Capacity. Explicit manual analyzer
nodes retain their Human Processing Capacity settings. An empty test menu retains the existing meaning of accepting any
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

Equipment nodes remain Equipment nodes in the editor. At execution, physical
chemistry analyzer bindings resolve to analyzer processing in a run-local copy,
including legacy projects, without rewriting their graphs. Test menus are
available for these bound nodes. Completed test codes travel with the order to
the reporting station and appear in the results feed. With no selected menu,
generated orders request `SIM-TEST`, an explicit simulation placeholder; select
test codes to simulate those tests. Numeric assay measurements are not generated.

### Saved scenarios

Use **Simulation → Scenarios** after opening a project, or **Manage** beside the
Workflow window's active-scenario selector. **Add Scenario** opens settings before creating
anything. Create from defaults, edit, duplicate, delete, or run a selected
scenario. Each configuration saves an explicit staff count, generated-order
arrival interval in minutes (0 releases orders together), and optional equipment
and human variation (0.10 means 10%). Duplicates retain processing overrides and
other existing parameters; edits preserve the scenario ID. Keep at least one
scenario per workflow.

The floor's **Setup & Equipment** panel shows saved scenario settings and fills
**Staff for this run** with the resolved staff count. Changes there affect only
the next run unless **Save to scenario** is selected. Save the project to retain
scenario changes on disk. Scenario controls are disabled while a live run is
playing, paused, or saving. Older scenarios retain their implicit defaults until
edited or explicitly saved.

### Named simulation workforce and node permissions

Edit a scenario to add, rename, or remove simulated staff. Select an analyzer or
process node in the Workflow Editor, open **Edit Properties**, and click
**Allowed staff** beside the staffing settings. This edits the active scenario
shown in the node window. You can also select a node in the scenario's
permissions table and click **Allowed staff**. Select names or use **All staff**
to include the entire roster, including people added later. These permissions
belong to the simulation scenario and do not change LIS accounts or roles.

**Required staff** remains a node property: that many eligible people must be
free together. Workers are shared across all nodes and are reserved as a complete
team. Zero required staff means unattended work. With a positive human rate,
workers are held during human processing (manual analyzers hold them during the
tests; preparation batches share one team). With no separate human rate, a
positive required count reserves the team during node processing. Runs reject
insufficient eligibility or assignments to removed staff; edit the affected
assignments before running. Renaming staff preserves assignments through IDs.

The floor displays worker names at their stations and distinguishes waiting for
staff from waiting for equipment/node capacity. Save the project to persist the
roster and scenario-specific node assignments. Scenario duplication copies both
without sharing mutable state. A run-only headcount override retains the first
N roster members; added workers receive generated names. If an override removes
an explicitly assigned worker, update the roster/assignments or choose a larger
headcount before running.

### Active scenario

Choose **Active scenario** in the Workflow Editor or Floor View, or select a
scenario in Manage Scenarios and click **Use scenario**. One scenario is active
per workflow. It supplies the default for repeated runs and node staff
permissions until you select another. Save the project to retain the selection
when reopening it. Choosing a different scenario in the Run dialog makes that
scenario active when you click Run; cancelling does not change the selection.

Node configuration labels distinguish shared node settings (including Required
staff, capacity, processing rates, and batching) from Allowed staff for the
named active scenario. Editing a different scenario in the manager does not
automatically make it active. If the active scenario is deleted, the first
remaining scenario becomes active. Older projects default to their first scenario.

### Show or hide simulation navigation

Open **File → Program Settings** and toggle **Show Simulation menu**. Saving
updates the menu immediately and remembers the preference across launches.
Simulation is shown by default for existing installations. Laboratory Design,
Workflows, Scenarios, and simulation run/result entries are grouped under this
menu. LIS navigation and database settings remain available when it is hidden.

### Load simulated orders for LIS demonstrations

In **Orders**, select **Simulation** as the data source and choose a scenario.
Set **Number orders** and click **Load Simulated Orders**. There is no saved-run
picker, test-mapping step, or extra import confirmation. Repeated clicks generate
fresh pending batches using the selected scenario; choosing the Simulation
source again lets you change scenarios. This works with the Simulation menu
hidden, and saved standalone workflows can be used without an open project.

Orders use the real LIS test codes selected on reachable analyzers in the
scenario's workflow. When analyzer menus are unrestricted, generation cycles
through active LIS catalogue tests instead of inventing `SIM-TEST`. Inactive or
missing configured codes produce a configuration error before anything is saved.
The scenario's arrival interval determines the generated order timestamps.

Loading creates only pending LIS records. **Analyze** subsequently runs tests
through the chosen workflow. Order generation does not execute or create a
management/research simulation run. Each load is atomic and receives its own
batch ID; the Simulation source displays all batches for the selected workflow
and scenario, so loading adds orders to the existing list. Previous batches retain
all results, reporting, receipt, and approval state and remain available under
SQLite. Older saved-run imports also remain accessible there.

Generated customers have batch-scoped `DEMO-` HNs and `[DEMO]` names, Unknown
gender, and placeholder DOB 2000-01-01 documented in their address. Orders have
real catalogue test items without results or approvals. Generation provenance
is saved in `lis_demonstration_orders` with the batch, workflow, and scenario IDs.

### Analyze LIS tests through the active workflow

Double-click a LIS order to open its test list. Select one or more rows and click
**Analyze**, or click **Analyze All** for all eligible pending tests in that
order. **LIS → Analyze** offers the same actions across all pending LIS tests.
Finished, reported, approved, cancelled, or rejected work is skipped. These
controls also work for orders imported from saved simulation runs.

Open a workflow to make it active; its active scenario supplies workforce,
permissions, processing rates, and variation. The analysis dialog names the
workflow and scenario before execution. With no project open, the selected
saved workflow is used (or the sole saved workflow when there is no selection).
Analysis requires one reachable Specimen Registration/reception node connected
to the rest of the workflow, and analyzers supporting the requested test codes.

Each selected test is submitted as a specimen to the actual SimPy workflow
engine. Unreceived work enters reception; received work starts through the
outgoing routes after reception. Named staff, shared equipment capacity,
batching, routing, and downstream workflow processing remain in effect. Existing
tests are submitted together, rather than using the scenario's generated-order
arrival interval. Original receipt times are preserved. New receipt and analyzer
completion timestamps come from the simulated event times in hours.

On successful completion, LIS items receive simulated values when no value was
already entered, a completion time, an updater, and a simulation-run comment.
Executed Result Reporting and Result Validation steps set reporting and approval
timestamps from their simulation completion events and attribute them to the
acting LIS user. The user must have the reporter/approver role respectively;
missing permissions abort the transaction. Workflows without those executed
steps leave the corresponding timestamps empty for manual LIS actions.
Validation requires reporting first, and a reporting step requires a result.
A visual reporting endpoint alone does not report or approve results. An order
is released only once every non-cancelled item is approved. Previously completed
results are not overwritten or backfilled. Failure saves no LIS changes. Each execution stores its operator, selected item IDs, workflow/scenario
snapshot, specimen event histories, and elapsed simulation time in
`lis_workflow_analyses`. The dialog reports the saved run ID and simulated elapsed
minutes; this is an engine execution without opening the Floor Plan.

Both **Analyze** actions open a workflow-selection modal listing current-project
and saved standalone workflows. The sole available workflow is preselected;
otherwise the previous analysis choice is preselected when still available.
Clicking Analyze saves that choice as the default across launches and displays
the selected workflow's active scenario. Cancelling does not change the default.

The Orders data-source choice is saved in Program Settings storage and restored
on app startup. Simulation remembers its workflow and scenario and restores the
existing scenario order list without generating another batch. If the original
project is closed, a saved workflow definition keeps the selection usable.
Uploaded-file selection remembers the file path and reloads it on startup,
without putting patient rows in settings. An unavailable saved source falls back
to SQLite with an explanation while retaining the preference for recovery.

### Random order arrivals

In **Manage Scenarios → Edit**, choose **Fixed** or **Exponential** under
**Arrival distribution**. The interval is in minutes: Fixed uses that exact gap;
Exponential samples random gaps with that mean. A mean of 3 minutes corresponds
to an average of 20 orders/hour. Fixed permits 0 to release all orders together;
Exponential requires a positive mean. The first order starts at time zero.

An optional **Arrival seed** repeats the same gaps for the same settings and
order count. Floor/Run Workflow's run seed overrides the arrival seed when set.
Arrival randomness is separate from processing-time randomness. Both floor Play
and **Orders → Load Simulated Orders** use the scenario's arrival distribution;
existing orders are unchanged. Save the project to retain scenario settings.
Older scenarios continue using Fixed intervals.

### Simulated specimen rejection

Set **Specimen rejection probability (%)** in **Manage Scenarios → Edit**.
The default is 0%; values from 0 to 100 are allowed. Each specimen is checked
independently after Specimen Registration/reception finishes. A rejected specimen
stops before analysis, with no test results or reporting, and appears in the
floor's rejected count. Small batches may differ from the configured percentage.
A run seed (or the scenario's arrival seed when no run seed is set) repeats the
decisions without changing the arrival or processing random streams.

**Load Simulated Orders** still creates pending LIS orders. Running **Analyze**
uses the workflow's active scenario and saves reception rejection time, reason,
and operator on rejected orders, with the trace retained in the analysis run.
Orders already accepted in LIS skip the reception rejection check. Manual
Accept/Reject remains available. Floor-generated simulation order metadata also
retains rejection details when the run is saved. Save the project to retain the
scenario's rejection probability.

### Lab Performance dashboard

Use **Lab Performance** on the floor workspace to open order/test counts and
median pre-analytical, analytical, post-analytical, and overall lab TAT in minutes.
Choose **Current floor run** (automatically refreshed) or **LIS database** (Refresh
reloads records). The LIS view covers the selected database, not just the project.

Floor-run milestones are arrival, first test start, last test finish, and result
validation completion. LIS milestones are order time, receipt, last active test
completion, and order approval. The dashboard labels these definitions. Medians
are per order and use only non-rejected/non-cancelled orders with all four
chronological milestones. Counts include all records in the selected source;
an empty TAT sample displays a dash instead of zero.

### Build Windows on GitHub Actions

Commit and push `.github/workflows/build-windows.yml` to the repository's default
branch. On GitHub, open **Actions → Build Windows app → Run workflow**, choose the
branch to package, and run it. The workflow builds a Windows x64 folder-based
application using Python 3.12 and PyInstaller. It includes artwork, orchestration
resources, and the Windows keyring backend; local config and database files are
not bundled. No signing credentials or repository write permissions are needed.

When the run succeeds, download **LabTycoon-Windows-x64** from its **Artifacts**
section. Extract the whole ZIP, then run `LabTycoon.exe`. Keep the `_internal`
folder beside the executable; users do not need a separate Python installation.
The artifact expires after 14 days. This is a portable app folder, not an installer.

On Windows, verify first-run administrator setup, sign-in persistence, opening a
project, the demo simulation, and Lab Performance. Runtime data defaults to
`%APPDATA%\LabTycoon`; the signing key is created in Windows credential storage.
The workflow's packaged `--help` check verifies the entry point, not full GUI
functionality. The build is unsigned, so Windows may display publisher warnings.

To build an older tag, that revision must contain the workflow. The existing
`2026.3-beta` tag predates this workflow; use the updated branch for the first
build. Adding the workflow does not move existing tags or publish a release.
