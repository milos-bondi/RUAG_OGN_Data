# Agents Instructions

You are working on a data collection and visualization for Open Glider Network. The goal is to create a simple html dashboard to visualize the data and a data collection job and keeps collecting new data

- Use `ogn-client` to collect the data. 
- Use `sqlalchemy` for the python ORM, no migrations are needed.
- Use `fastapi` for the api.
- Use `pydantic-settings` to load enviroments, create a `env = Settings()` object
- Use `uv` to manage the python enviroment.
- A single `uv run fastapi` that run both the api and the cron.

# Architecture

```
repository/
├── src/                # API application code
│   ├── envs.py         # Environment variable loading
│   ├── router.py       # Router assembly and API wiring
│   ├── cron/           # Scheduled background jobs / data collection
│   ├── db/             # Database session, models, and queries
│   │   ├── __init__.py # Package marker
│   │   ├── session.py  # Database session setup
│   │   ├── functions/  # Database helper queries
│   │   └── models/     # ORM models
│   ├── dtypes/         # Pydantic schemas and API data types
│   ├── routes/         # API route handlers
│   ├── utils/          # Shared utilities  
│   ├── static/         # Static files (html pages, ecc.)
│   ├── .env.sample     # Development enviroments variables
│   └── pyproject.toml
│
├── AGENTS.md
└── README.md 
```

# Code Style Guide 

- Keep changes small and clear.
- Remove obsolete code when replacing old flows.
- Use built-in types for type hints list, dict
- Sort the imports by length, starting with import and then from
- Use | for union types instead of Optional
- All Python functions must include docstring (""" ... """) immediately after definition.
- Any non-trivial Python logic block must have standalone inline comment (# ...) above block.
- Include two blank lines between function definitions.
- Write test cases only when instructed
- Create a function when it gives you a meaningful abstraction boundary. Do not create one just to “split code”.
- Keep improving and cleanup the repository so that it follows the described architecture
- Cleanup any .md file that is not strictly needed, keep the `AGENTS.md` and the `README.md only
- Make sure to include in the `README.md` all the instructions on how to run the server.