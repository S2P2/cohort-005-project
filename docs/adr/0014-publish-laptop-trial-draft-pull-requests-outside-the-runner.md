# Publish laptop trial draft pull requests outside the runner

The laptop trial runs the existing Sandcastle `agent:implement` task with Pi through the private Magpie gateway in a disposable runner. The runner returns a Git bundle, a pull request summary, and the run outcome. The orchestrator verifies the result and uses its own git host credentials to push the branch, open the linked draft pull request, and report the outcome on the ticket.

Publishing from the runner would give repository code access to publication credentials and tie publication to the runner’s lifetime. Keeping publication with the orchestrator lets it resume after the runner is gone. If the laptop restarts after pushing the branch, the orchestrator checks for the branch and any draft pull request for that run before completing publication.
