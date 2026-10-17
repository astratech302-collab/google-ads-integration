# Host the MVP on AWS

The supplied deployment is a **single Linux host with persistent storage**, running
the API plus Caddy for HTTPS. On AWS, an EC2 instance with encrypted EBS is a practical
first host for this SQLite MVP. The service does not call AWS APIs or require AWS
credentials inside the container. This guide does not create AWS resources.

## Local verification first

Run the local README commands and `scripts/smoke_test.py`. Configure Google OAuth
locally and test a permitted test account before switching production access on.
The container uses Python 3.14, UID 10001, one Uvicorn process, and no access logging.

## Prepare the host

1. Use an existing supported Linux EC2 instance, or launch one through your AWS
   console. A small instance with at least 2 GiB RAM is a reasonable starting point;
   measure memory for your actual account sizes. Use encrypted EBS for the Docker
   data directory and ensure the volume is retained/backed up if the instance is
   replaced. SQLite must stay on local block storage, not a shared network filesystem.
2. Install Docker Engine and the Docker Compose plugin following the supported
   instructions for your distribution. Access the host through your established
   administration method; AWS Systems Manager Session Manager avoids inbound SSH.
   Require IMDSv2. This application needs no instance metadata access.
3. Give the host a stable public address and point your hostname, e.g.
   `ads-api.example.com`, to it. Permit inbound TCP 80/443 for Caddy; do not expose 8000.
   Permit outbound HTTPS/DNS to Google and the certificate authority. If you enable
   redirect resolution, permit the allowlisted destinations as well.
4. Copy this repository to the host. Create `.env` from `.env.example` with
   `ADS_MODE=live`, strong API keys, an explicit stable encryption key, OAuth client
   credentials, `ADS_DOMAIN=ads-api.example.com`, and
   `ADS_REDIRECT_URI=https://ads-api.example.com/oauth/callback`.
   Register the exact HTTPS callback on your Google OAuth client. Keep `.env` readable
   only by the deployment operator (`chmod 600 .env`); keep a separate protected backup
   of the encryption key. API keys belong only in your trusted calling services.

## Start and verify

From the repository root:

```sh
docker compose --env-file .env -f deploy/compose.aws.yaml up -d --build
docker compose --env-file .env -f deploy/compose.aws.yaml ps
curl --fail https://ads-api.example.com/health
```

Caddy obtains and renews the certificate after public DNS resolves and ports 80/443
are reachable. No request access log is enabled. Do not add proxy logs containing
OAuth callback query strings. The API's responses include `Cache-Control: no-store`.
If your existing reverse proxy already provides TLS, route to the private API port
instead and retain that callback logging restriction.

Use your trusted service to call `/oauth/start` with its `X-API-Key`, complete consent,
discover accounts, select a permitted child, and request a report. Confirm a report
can still be retrieved after restarting the containers. Then revoke the connection
and confirm requests return 401. New Google projects need production access approval
before querying real accounts; mock mode is available throughout development.

The `ads-data` named volume survives normal container replacement on the same host.
It does **not** survive losing the underlying EBS volume or an unplanned replacement
of the whole host. Back up the database with SQLite's backup API or stop the API while
copying the volume. Back up the encryption key separately. Restore both to recover
credentials. Never run `docker compose down -v` on a deployment whose data you need.

## Operational limits and next hosting step

Keep a single API replica while it uses SQLite and a process-local request pacer.
Restrict who can trigger expensive reports through your calling service; no public
end-user authentication system is bundled. Reports are synchronous, so your proxy
and integration HTTP timeouts must allow the intended account size. Use measured
request limits/timeouts; large accounts should move to a queued job model rather
than raising timeouts indefinitely.

For managed ECS/Fargate hosting, first replace the SQLite store with a shared durable
database and move large reports to object storage, then deploy a stateless container
behind HTTPS. Do not place the current database on Fargate ephemeral storage or share
SQLite across replicas via EFS. For an ECS deployment, inject secrets through the
container definition's `secrets` references, keep execution and application task roles
separate, and redeploy tasks after rotating injected secrets. This repo does not
claim to include that database migration or an ECS infrastructure deployment.

Sources: [EC2 security](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-security.html),
[EBS encryption](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-encryption.html),
[ECS/Fargate task configuration](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_definition_parameters.html),
[ECS secrets injection](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/specifying-sensitive-data.html).
