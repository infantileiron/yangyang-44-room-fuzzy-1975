import hashlib
import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.config.project import SPEC
from src.constants.enums import Decision, RunStatus
from src.domain.analysis import analyze
from src.domain.compare import compare
from src.models.workflow import AnalysisRun, AuditEvent, Comparison, Observation, Review
from src.services.errors import BusinessError
from src.services.validation import validated
from src.utils.formatting import timestamp


def _fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def snapshot_key(run):
    """对照观测指纹：同一批固化观测加同一组分析条件，重跑视为同一次观测。"""
    return _fingerprint({'observations': run.observation_ids, 'parameters': run.parameters})


def _params_key(params):
    return _fingerprint({'comparison': params})


def _run_payload(db, run):
    result = run.result or {}
    clusters = result.get('clusters')
    if not clusters:
        sample = list(db.scalars(select(Observation.payload).where(Observation.id.in_(run.observation_ids))
                                 .order_by(Observation.id)))
        clusters = analyze(sample, run.parameters)['clusters']
    return {'clusters': clusters, 'rows': result.get('rows', [])}


def _find(db, baseline_run_id, snap_key, par_key):
    return db.scalar(select(Comparison).where(Comparison.baseline_run_id == baseline_run_id,
                                              Comparison.snapshot_key == snap_key,
                                              Comparison.params_key == par_key))


def create_comparison(db, request, actor):
    params = validated(SPEC['comparison_parameters'], request.parameters, defaults=True)
    if request.baseline_run_id == request.candidate_run_id:
        raise BusinessError('基准与对照不能是同一份分析', 409)
    baseline = db.get(AnalysisRun, request.baseline_run_id)
    candidate = db.get(AnalysisRun, request.candidate_run_id)
    if baseline is None or candidate is None:
        raise BusinessError('分析不存在', 404)
    review = db.scalar(select(Review).where(Review.run_id == baseline.id, Review.decision == Decision.APPROVED))
    if baseline.status != RunStatus.SUCCEEDED or review is None:
        raise BusinessError('基准必须是已签核通过的分析结果', 409)
    if candidate.status != RunStatus.SUCCEEDED:
        raise BusinessError('对照分析尚未计算完成', 409)
    snap_key, par_key = snapshot_key(candidate), _params_key(params)
    found = _find(db, baseline.id, snap_key, par_key)
    if found:
        return found, False
    result = {
        'baseline': {'id': baseline.id, 'title': baseline.title, 'reviewer': review.reviewer,
                     'reviewed_at': timestamp(review.created_at)},
        'candidate': {'id': candidate.id, 'title': candidate.title},
        **compare(_run_payload(db, baseline), _run_payload(db, candidate), params['tolerance']),
    }
    item = Comparison(baseline_run_id=baseline.id, candidate_run_id=candidate.id,
                      snapshot_key=snap_key, params_key=par_key, parameters=params,
                      result=result, created_by=actor)
    db.add(item)
    try:
        db.flush()
    except IntegrityError:
        # 并发提交了同一基准、观测和参数的对照：回滚后沿用先写入的那份。
        db.rollback()
        found = _find(db, baseline.id, snap_key, par_key)
        if found is None:
            raise BusinessError('相同对照正在并发写入，请刷新后重试', 409)
        return found, False
    db.add(AuditEvent(actor=actor, action='comparison.created',
                      detail=f'对照 #{item.id} · 基准 #{baseline.id} ← 分析 #{candidate.id}'))
    db.flush()
    return item, True


def list_comparisons(db, candidate_run_id=None):
    query = select(Comparison).order_by(Comparison.id.desc()).limit(100)
    if candidate_run_id is not None:
        run = db.get(AnalysisRun, candidate_run_id)
        if run is None:
            raise BusinessError('分析不存在', 404)
        query = select(Comparison).where(Comparison.snapshot_key == snapshot_key(run)) \
            .order_by(Comparison.id.desc()).limit(100)
    return list(db.scalars(query))


def get_comparison(db, comparison_id):
    item = db.get(Comparison, comparison_id)
    if item is None:
        raise BusinessError('对照不存在', 404)
    return item
