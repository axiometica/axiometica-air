# Screenshots Directory

This directory contains visual assets for the GitHub repository README and documentation.

## File Structure

```
docs/screenshots/
├── dashboard.png              # Main incident dashboard with list and metrics
├── incident-detail.png        # 5-tab incident detail view
├── agent-pipeline.png         # 7-agent pipeline flow visualization
├── runbook-editor.png         # Visual runbook editor with decision nodes
├── cmdb-graph.png             # Neo4j force graph showing relationships
├── approval-queue.png         # CAB approval queue interface
├── metrics-dashboard.png      # Watcher metrics and health checks
├── slack-integration.png      # Slack ChatOps notifications
└── README.md                  # This file
```

## Adding Screenshots

### How to Capture Screenshots

1. **Start the platform locally:**
   ```bash
   docker compose up -d
   npm run dev --prefix frontend
   # Frontend at http://localhost:3000
   ```

2. **Log in** with default credentials:
   - Email: `admin@platform.local`
   - Password: `admin`

3. **Capture key workflows:**
   - Dashboard with active incidents
   - Incident detail view (click any incident)
   - Approval queue (if approvals are pending)
   - CMDB graph (click incident's CMDB tab)
   - Runbook editor (Settings → Runbooks → Edit any runbook)

4. **Screenshot tips:**
   - Use dark mode (platform default)
   - Capture at 1280x720 minimum resolution
   - Include browser window chrome for context
   - Add 20px white padding around edges
   - Use PNG format (no JPEG)

### Naming Convention

- Use kebab-case names: `dashboard.png`, `incident-detail.png`
- Describe what's shown, not the feature: `approval-queue.png` not `feature-3.png`

### Size Recommendations

- Max 1.5 MB per image
- Optimal: 1280x720 to 1920x1080
- GitHub renders at 100% width on desktop, 100vw on mobile

### Updating README.md

Screenshot references in `README.md` use this format:

```markdown
![Description of the screenshot](./docs/screenshots/filename.png)
*Italicized caption explaining what's shown*
```

The images are linked relative to the repository root.

---

**Note:** Screenshots are not version-controlled due to size. If you update the UI, re-run screenshots to keep docs current.
