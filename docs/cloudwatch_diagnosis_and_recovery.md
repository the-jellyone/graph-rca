# CloudWatch diagnosis and recovery

Checked against AWS documentation on 20 October 2026.

CloudWatch investigations does not promise automatic recovery for every root
cause it identifies. It produces observations, likely causes and remediation
suggestions. In a configured investigation, users can execute suggested Systems
Manager Automation runbooks. That means the feature goes beyond RCA alone,
but execution still needs a suitable runbook, selected resources and permissions.

The stages are different mechanisms:

| Stage | Mechanism |
| --- | --- |
| Detection | Metric alarms can use fixed thresholds or statistical and machine learning anomaly bands. |
| Investigation | Generative AI examines metrics, logs, traces, deployment events and resource relationships to propose likely causes. Application Signals and X-Ray help establish dependencies. Without them, investigations can infer relationships from co-occurring anomalies. |
| Recovery | Configured alarm actions, Systems Manager runbooks, Lambda functions or EventBridge integrations perform defined operations. |
| Verification | The chosen automation must check the resulting resource state or application health. Runbooks support assertions, waits, branching, retries and approval steps. |

Lambda is not mandatory for every repair. CloudWatch supports built-in EC2
reboot/recovery and Auto Scaling actions. Systems Manager has existing runbooks
and supports custom ones. Application-specific faults may need custom commands,
scripts, Lambda code or another orchestration mechanism. A root-cause hypothesis
does not itself provide a safe implementation of a repair.

Our demo also uses an explicit recovery implementation. A policy selects
evidence, the cause-set model supplies a diagnosis, and the controller allows
only recorded repairs of its owned injected components. It checks confidence,
purchased mechanism evidence and health afterward. Neither this demo nor
CloudWatch should be described as fixing arbitrary faults without a defined
recovery action. Our research comparison concerns budgeted evidence selection
and its accuracy, cost and decision time. We have not benchmarked against AWS.

Sources:

- [CloudWatch investigations](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Investigations.html)
- [How investigations finds data](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/data-usage-considerations.html)
- [CloudWatch anomaly detection](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Anomaly_Detection.html)
- [CloudWatch alarm actions](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/alarm-actions.html)
- [Systems Manager Automation actions](https://docs.aws.amazon.com/systems-manager/latest/userguide/automation-actions.html)
