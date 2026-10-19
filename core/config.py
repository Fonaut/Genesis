import os

# Genesis Configuration Settings

# Whether to print private chain-of-thought reasoning to the terminal.
# Defaults to False to keep the conversational terminal output clean and private.
# Can be enabled by setting SHOW_INTERNAL_THOUGHTS=true in the environment.
SHOW_INTERNAL_THOUGHTS: bool = os.getenv("SHOW_INTERNAL_THOUGHTS", "false").lower() in ("true", "1", "yes")
