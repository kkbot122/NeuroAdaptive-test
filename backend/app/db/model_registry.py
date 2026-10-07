"""Register every SQLAlchemy model for non-HTTP processes such as Celery."""
# Import side effects are intentional: SQLAlchemy relationship strings resolve
# through Base's registry. FastAPI imports these through app.main; workers must
# do the same before opening a session.
from app.modules.auth import models as auth_models
from app.modules.content import models as content_models
from app.modules.profiling import models as profiling_models
from app.modules.chat import models as chat_models
from app.modules.events import models as events_models
from app.modules.assessment import models as assessment_models
from app.modules.courses import models as courses_models
from app.modules.documents import models as documents_models
from app.modules.documents import chunk_models as chunk_models
from app.modules.jobs import models as jobs_models
from app.modules.curriculum import models as curriculum_models
from app.modules.mastery import models as mastery_models
from app.modules.adaptation import models as adaptation_models
from app.modules.learning import models as learning_models
from app.modules.tutor import models as tutor_models
from app.modules.abuse import models as abuse_models
from app.modules.audit import models as audit_models
from app.modules.evaluation import models as evaluation_models
from app.modules.preparation import models as preparation_models
