# Database agent on the user's own server (new direction, agreed 27 Oct 2026; after the 1 Nov launch)

Replaces "connect your database on our portal". Passwords and data never leave the user's server.

## Shape
- One agent per server (not per website). It finds every website and database on that server itself
  (wp-config.php, .env, CodeIgniter 3/4, Yii2, Symfony, Joomla, Drupal, Magento, OpenCart, cPanel accounts...).
- Same reasoning everywhere: the 5-layer core + families + dialects (docs/db-five-layers.md), checking the live database.
- Read-only by default; any change needs preview, approval and Undo.
- Agents dial out only (no open ports). Results, never passwords or raw data, go to the dashboard.
- Dashboard: servers -> websites -> databases, health colour, problems, alerts, client/project tags. On our portal,
  or self-hosted for full privacy.
- Signed updates: new knowledge and new databases arrive as signed packages. Learning from misses goes back only with
  consent and without data.

## Per environment
| Where | Agent | Covers |
|---|---|---|
| Linux VPS / dedicated / AWS EC2 | one command or Docker | all sites and databases on the server |
| VPS with cPanel/WHM (root) | server level | every cPanel account |
| AWS RDS / Aurora | agent on an EC2 in the same network | the RDS endpoints (later: AWS monitoring) |
| Windows (IIS, XAMPP) | Windows service | IIS/XAMPP sites |
| Shared hosting (no root) | light agent: a PHP file run by cPanel cron every few minutes, pushes results out | all sites in that account |

## User journey
1. Sign in on Goal Engine -> "Add a server" -> pick the kind of server -> copy one command (or the cron line).
2. Run it once. The agent appears in the dashboard within a minute, linked by a one-time token.
3. It lists what it found ("37 websites, 41 databases") and the user ticks which to watch.
4. First health check runs straight away: a report per database (problems by layer, cause, proof, fix, prevention).
5. From then on: checks on a schedule, alerts by email when something goes wrong, questions in plain words
   ("why is site X slow", "find duplicate students"), fixes only after approval with Undo.
6. Remove: one uninstall command, or delete the agent from the dashboard (it stops and forgets its token).

## Order
1. Linux agent + dashboard (VPS, EC2, WHM) ~ 1 week.
2. Shared-hosting cron agent.
3. Windows service.
Open decisions (Chetan): dashboard on our portal, self-hosted, or both; code protected (compiled + licence) or open;
free or paid.
