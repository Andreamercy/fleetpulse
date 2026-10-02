Feature: Fleet breakdown-risk monitoring
  As a fleet manager I want vehicles ranked by breakdown risk and critical faults flagged in real time
  so that I can service the right vehicles before they fail.

  Scenario: Sustained overheating raises a critical alert
    Given a vehicle streaming normal telemetry
    When its coolant temperature stays above 110 C for one minute
    Then an OVERHEAT alert with severity 5 is raised exactly once

  Scenario: Duplicate and malformed events never corrupt results
    Given a vehicle streaming normal telemetry
    When the same event is delivered twice and a message with an invalid VIN arrives
    Then only one copy is stored and the bad message is in the dead-letter queue

  Scenario: A manager sees only their own fleet
    Given a fleet manager of tenant "t00"
    When they request the vehicle of tenant "t01"
    Then the API answers 404 exactly as for a vehicle that does not exist

  Scenario: The agent cannot book maintenance without human approval
    Given a fleet manager of tenant "t00"
    When they ask the agent to book a work order for one of their vehicles
    Then the work order is PENDING_APPROVAL until a manager approves it
