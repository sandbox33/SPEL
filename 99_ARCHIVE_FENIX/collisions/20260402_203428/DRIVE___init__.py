import os
if os.environ.get("HOLMES_ALLOW_SANDBOX") != "true":
    raise ImportError(
        "SANDBOX_MODULE: not for production import (R40/EF-25). "
        "Set HOLMES_ALLOW_SANDBOX=true only in Colab testing."
    )
