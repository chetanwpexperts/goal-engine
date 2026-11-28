"""Request and response schemas, grouped by area."""

from app.domain.schemas.expert import ExpertIn, ExpertUpdate
from app.domain.schemas.learning import LearnVoteIn, LearnAskIn, LearnStatusIn
from app.domain.schemas.chat import MessageIn, StatusIn
from app.domain.schemas.connector import ConnectorIn, DirectIn
from app.domain.schemas.db_jobs import DbJobIn, DbReportIn, DiagnoseIn, OptimizeIn, RecognizeIn, PlanIn, ApplyIn, UndoIn, ExcelIn
from app.domain.schemas.agents import StartIn, OwnerIn
from app.domain.schemas.office import OfficeStartIn, OfficeFixIn, OfficeCompareIn
