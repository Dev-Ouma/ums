# UMS Data Protection Review

Status: **implementation baseline; institutional approval required before go-live**

This review turns the UMS privacy requirements into an operational control set. It does not determine the university's legal bases, retention periods, controller/processor roles, or territorial scope. The Data Protection Officer (DPO) and institutional counsel must approve those decisions.

## Current application controls

| Control | Current state | Evidence / owner |
| --- | --- | --- |
| Public privacy notice | Implemented, with institution-specific placeholders | `/privacy/`; DPO to approve wording and contacts |
| Data subject export | Implemented for signed-in account and identity data; excludes passwords, hashes, reset tokens and session keys | `accounts:data/export`; Security Administrator to test scope |
| Data correction | Implemented for self-service profile fields; registry-managed fields remain administrator-controlled | `accounts:profile_settings`; Registrar/HR owner |
| Access control | RBAC and record-level authorization are enforced server-side | Security Administrator; IDOR test suites |
| Audit trail | Authentication, administrative changes, exports and sensitive workflows are audited | Security Administrator; retention schedule required |
| Recycle bin | Deleted operational records can be restored or permanently purged by authorized staff | Registrar/Finance/DPO to define exceptions |
| Backups | Encrypted/protected backup and retention workflows exist | Backup owner; restore evidence required |
| Breach response | Incident-response and go-live controls exist | Incident Response Team; named contacts and notification runbook required |

## Required data inventory

The university must maintain one row per processing activity with: data category, subjects, fields, source, purpose, lawful basis, special-category assessment, recipients, processor, location, transfer mechanism, retention period, deletion/anonymisation action, owner, access roles, encryption, audit evidence, and DPIA requirement.

| Processing area | Typical UMS data | Purpose | Decision required |
| --- | --- | --- | --- |
| Identity and access | name, username, email, phone, role, login and security events | account administration and security | Registrar/Security/DPO approve basis and retention |
| Admissions and student records | application, student number, programme, academic history and documents | admissions, registration and statutory academic administration | Admissions/Registrar/DPO approve schedule |
| Finance and payments | invoices, balances, provider references, receipts and reconciliation | fees, payment verification and accounting | Finance/DPO approve schedule; never store prohibited card data |
| Staff and HR | employee identity, department, designation and employment status | workforce lifecycle and access provisioning | HR/DPO approve source-of-truth and retention |
| Communications | email, phone, notifications and delivery events | service notices and operational communications | Communications/DPO document opt-in basis where needed |
| Security and operations | IP, device/user agent, audit events, errors and backups | abuse prevention, investigation, resilience | Security/DPO approve minimisation and retention |
| Integrations | identifiers and minimum synchronized records | LMS, library, finance, HR, meetings and providers | Integration Administrator maintains processor/transfer register |

## Retention schedule to approve

These are control categories, not legal durations. Each duration must be replaced with an approved institutional/legal period and implemented in scheduled deletion or anonymisation jobs.

| Record | Approved period | End action | Owner |
| --- | --- | --- | --- |
| Student and academic records | **TBD** | archive or controlled deletion where legally permitted | Registrar |
| Admissions and unsuccessful applications | **TBD** | delete or anonymise | Admissions |
| Financial, payment and reconciliation records | **TBD** | archive; preserve audit/accounting obligations | Finance |
| Uploaded documents | **TBD** | secure deletion and storage purge | DPO/Registrar |
| Audit and login history | **TBD** | purge or anonymise after investigation hold | Security |
| Application/security logs | **TBD** | rolling purge with incident hold | DevOps/Security |
| Backups | **TBD** | protected rolling expiry; verify deletion where supported | Backup owner |

The recycle bin and legal hold process must prevent deletion of final academic, payment, statutory or audit records without an approved exception. Restores and permanent purges must remain auditable.

## Rights-request workflow

1. Receive the request through the privacy contact and verify identity.
2. Classify access, correction, export, deletion, restriction or objection; record scope and due date.
3. Query only the requester's records and consult the data owner for academic, finance, HR or legal holds.
4. Apply redaction, third-party confidentiality and statutory-retention exceptions.
5. Provide a secure response or download, record the decision and close with evidence.

The self-service export is a starting point for access/portability. It is intentionally not a full database dump and must be supplemented by the DPO workflow for documents, academic, financial, integration and archived records.

## Processors and transfers

Create a register for payment gateways, email/SMS, LMS, HR/SMHR, finance/procurement, library, meeting providers, cloud storage, hosting, DNS, monitoring and backup vendors. For each, record the service, data fields, purpose, country, subprocessors, contract/DPA, security measures, deletion/return terms, breach contact, and transfer safeguard. No provider credential belongs in browser code, exports or logs.

## Breach procedure

The Incident Response Team must preserve evidence, contain access, identify affected systems/data subjects, involve the DPO, document risk, and make notifications required by applicable law. The Kenya DPA/ODPC notification route and any GDPR supervisory-authority or data-subject notification route must be confirmed in the institution's runbook. Test the runbook at least annually and after material system changes.

## Go-live blockers

Do not approve go-live while the university lacks an approved controller/DPO contact, data inventory, lawful-basis register, retention schedule, processor/DPA register, transfer assessment, rights-request procedure, breach contacts, or tested deletion/restore evidence. A placeholder privacy notice is not approval of those controls.

## Authoritative references

- Kenya Data Protection Act, 2019, latest consolidated text: https://new.kenyalaw.org/akn/ke/act/2019/24/eng@2022-12-31
- Kenya Data Protection (General) Regulations, 2021: https://new.kenyalaw.org/akn/ke/act/ln/2021/263/eng@2022-01-14
- ODPC breach reporting: https://www.odpc.go.ke/report-a-data-breach/
- GDPR Regulation (EU) 2016/679: https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679
