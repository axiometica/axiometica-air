# Contributing to Axiometica AIR

Thanks for your interest in making Axiometica AIR better! This document guides you through contributing — whether that's testing, reporting feedback, fixing bugs, or building new features.

---

## Table of Contents

1. [Code of Conduct](#code-of-conduct)
2. [Testing & Feedback (Early Community)](#testing--feedback-early-community)
3. [Local Development Setup](#local-development-setup)
4. [Running Tests](#running-tests)
5. [Submitting Feedback](#submitting-feedback)
6. [Contributing Code](#contributing-code)
7. [Documentation](#documentation)

---

## Code of Conduct

Be respectful. No harassment, discrimination, or bad-faith arguments. We're building this together.

---

## Testing & Feedback (Early Community)

**This is our primary goal right now.** We want community members to:

1. **Try it out** — Follow the [Quick Start Guide](docs/QUICKSTART.md)
2. **Test real scenarios** — Use it with your own incidents/changes
3. **Give honest feedback** — Tell us what works, what doesn't, what's confusing

### How to Give Feedback

**GitHub Discussions (preferred):**
- Go to [Discussions](../../discussions)
- Choose "Testing & Feedback" category
- Tell us:
  - What you tested (incident workflow, change workflow, dashboard, etc.)
  - What worked well
  - What was confusing or didn't work
  - What feature you'd want next
  - Would you use this? (yes/no/maybe)

**GitHub Issues:**
- Use for bugs only (crashes, errors, data loss)
- Use [bug report template](.github/ISSUE_TEMPLATE/bug_report.md)

**Direct message/Email:**
- If you prefer private feedback, reach out via GitHub Issues as "private feedback" and we'll contact you

---

## Local Development Setup

### Prerequisites

- **Docker Desktop** (v20.10+)
- **Docker Compose** (v2.0+, bundled with Docker Desktop)
- **Node.js** (18.0+) for frontend development
- **Python 3.11+** (optional, for local linting)
- **Git**
- **4+ GB RAM** available for Docker

### First-Time Setup

```bash
# Clone the repository
git clone https://github.com/axiometica/axiometica-air.git
cd axiometica-air

# Copy environment file (must be at the repo root, next to docker-compose.yml)
cp .env.example .env
# Edit .env and fill in any CHANGE_ME values — generate secrets with: openssl rand -hex 32

# Start all services
docker compose up -d

# Verify containers are running
docker ps

# Backend should be available at http://localhost:8000
# Frontend at http://localhost:80 (or http://localhost:3000 if running locally)
```

For detailed setup, see [`docs/DEVELOPMENT_GUIDE.md`](docs/DEVELOPMENT_GUIDE.md).

### Running Frontend Locally (Recommended for Development)

```bash
cd frontend
npm install
npm run dev

# Frontend now runs at http://localhost:3000 with hot reload
```

---

## Running Tests

### Backend Tests

```bash
cd backend

# Run all tests
pytest

# Run a specific test file
pytest tests/test_context_schema.py

# Run a specific test
pytest tests/test_context_schema.py::test_function_name

# Run with coverage
pytest --cov=agentic_os

# Run with verbose output
pytest -v
```

### Frontend Tests

```bash
cd frontend

# Type-check
npm run type-check

# Lint
npm run lint

# Build for production
npm run build
```

### Docker Tests

```bash
# Run tests inside the backend container
docker compose exec agentic_os_backend pytest

# View container logs
docker compose logs -f agentic_os_backend
```

---

## Submitting Feedback

### Feedback Template

When you test something, use this template in GitHub Discussions:

```
## What I Tested
- [ ] Getting started / deployment
- [ ] Dashboard
- [ ] Incident workflow
- [ ] Change workflow
- [ ] Approvals
- [ ] Other: _______________

## What Worked Well
(List 2-3 things that impressed you)

## What Was Confusing or Broken
(What didn't work, was hard to understand, or surprised you?)

## Feature Request
(Is there something you'd like to see added?)

## Overall Impression
- [ ] I'd definitely use this
- [ ] I'd use this with modifications
- [ ] Interesting but not for my use case
- [ ] Not ready yet

## Additional Context
(Logs, screenshots, environment details, etc.)
```

### How We'll Respond

This is maintained on a best-effort basis, not a guaranteed SLA — response times will vary. When we get to it, we'll either:
  - Fix the issue
  - Ask clarifying questions
  - Add it to our roadmap
  - Explain why we made a different choice

---

## Contributing Code

### Before You Start

1. **Check existing issues/discussions** — Someone might already be working on it
2. **Open an issue first** — Describe the change and why you want to make it
3. **Wait for feedback** — We might have suggestions or concerns

### Making Changes

1. **Create a branch**
   ```bash
   git checkout -b feature/my-feature
   # or
   git checkout -b fix/my-bug
   ```

2. **Make your changes**
   - Follow the [code style guide](#code-style) below
   - Write tests for new functionality
   - Update documentation as needed

3. **Run tests locally**
   ```bash
   # Backend
   cd backend && pytest

   # Frontend
   cd frontend && npm run type-check && npm run lint
   ```

4. **Commit with clear messages**
   ```bash
   git commit -m "fix: resolve incident approval timeout issue"
   git commit -m "feat: add CMDB refresh button to incident detail"
   ```

5. **Push and open a pull request**
   ```bash
   git push origin feature/my-feature
   # Then open a PR on GitHub
   ```

### PR Guidelines

- **Title:** Use conventional commits (`fix:`, `feat:`, `docs:`, etc.)
- **Description:** Explain *why* you made this change, not just what
- **Size:** Smaller PRs get faster review (try to keep under 400 lines)
- **Tests:** Include tests for new functionality
- **No breaking changes:** Unless discussed in an issue first

### Code Style

**Backend (Python):**
```bash
# Format with black
black src/ tests/

# Sort imports with isort
isort src/ tests/

# Lint with ruff (configured in pyproject.toml)
ruff check src/
```

**Frontend (TypeScript/React):**
```bash
# Format with Prettier (configured in package.json)
npm run format

# Lint with ESLint
npm run lint

# Type-check
npm run type-check
```

---

## Documentation

### When to Update Docs

- Adding a new feature → Update relevant docs in `docs/`
- Changing behavior → Update the feature docs
- New integration → Add a setup guide in `docs/`
- Fixing a gotcha → Add to [Troubleshooting](docs/WATCHER_TROUBLESHOOTING.md)

### Documentation Structure

```
docs/
├── QUICKSTART.md                   ← Getting started (10 minutes)
├── DEVELOPMENT_GUIDE.md            ← Local development setup
├── ARCHITECTURE.md                 ← System design
├── API_REFERENCE.md                ← REST/WebSocket endpoints
├── FEATURES.md                     ← Complete feature catalog
├── DEPLOYMENT_GUIDE.md             ← Production deployment
├── ADMIN_GUIDE.md                  ← Administrative tasks
├── SLACK_SETUP.md                  ← Slack integration
├── WATCHER_SETUP.md                ← Monitoring/watcher setup
└── WATCHER_TROUBLESHOOTING.md      ← Debug guides
```

### Documentation Tips

- Use examples — show code and expected output
- Keep it scannable — use headers, bullet points, tables
- Link to related docs — use relative paths `[link](../ARCHITECTURE.md)`
- Update CLAUDE.md — if you create a new doc, add it to the [Further Reading](#further-reading) section

---

## Questions?

- **Setup issues?** → Check [`DEVELOPMENT_GUIDE.md`](docs/DEVELOPMENT_GUIDE.md)
- **Architecture questions?** → See [`ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- **API details?** → See [`API_REFERENCE.md`](docs/API_REFERENCE.md)
- **Still stuck?** → Open a discussion in [GitHub Discussions](../../discussions)

---

## What's Next?

After testing/contributing, check out:
- **Roadmap** → Coming soon
- **Architecture deep-dive** → [`ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- **Join the community** → Star the repo, follow updates
- **Spread the word** → Tell other DevOps/SRE folks about Axiometica AIR

Thanks for contributing! 🎉
