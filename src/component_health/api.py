from contextlib import asynccontextmanager
import os
from threading import Lock
from typing import Literal
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from component_health.database import build_engine
from component_health.foundation import CONTRACT_VERSION,MIGRATION_HEAD,SERVICE
from component_health.settings import Settings


def create_app(settings:Settings|None=None,engine=None):
    settings=settings or Settings()
    engine=engine if engine is not None else build_engine(settings,read_only=True)
    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.dispose()
    app=FastAPI(title='Robot Component Health Monitor',version='0.1.0',lifespan=lifespan)
    @app.exception_handler(SQLAlchemyError)
    async def database_unavailable(request,exception):
        return JSONResponse(status_code=503,content={'detail':'Database is temporarily unavailable; retry shortly'})
    app.state.engine=engine
    app.state.reader=None;reader_lock=Lock()
    def reader():
        with reader_lock:
            if app.state.reader is None:
                from component_health.reader import Reader
                app.state.reader=Reader(engine,settings.raw_directory,settings.analytical_directory)
        return app.state.reader
    def check_range(start,stop):
        if start>stop:raise HTTPException(422,'Capture range starts after its end')
    @app.get('/healthz')
    def health():
        return {'status':'alive','service':SERVICE,'scope':'foundation'}
    @app.get('/readyz')
    def ready():
        try:
            with engine.connect() as connection:
                revisions=connection.scalars(text('SELECT version_num FROM alembic_version')).all()
                versions=connection.scalars(text('SELECT contract_schema_version FROM platform_metadata WHERE singleton_id=1')).all()
            if revisions!=[MIGRATION_HEAD] or versions!=[CONTRACT_VERSION]:
                return JSONResponse(status_code=503,content={'status':'not_ready','reason':'contract_or_migration_mismatch'})
            for directory in [settings.raw_directory,settings.analytical_directory]:
                if not directory.is_dir():raise OSError('Storage path missing')
                with os.scandir(directory) as entries:next(entries,None)
        except Exception:
            # Never expose connection strings, database exceptions or local paths.
            return JSONResponse(status_code=503,content={'status':'not_ready','reason':'database_or_storage_unavailable'})
        return {'status':'ready','contract_version':CONTRACT_VERSION,'migration_head':MIGRATION_HEAD,'scope':'foundation'}
    @app.get('/api/v1/platform')
    def platform():
        return {'service':SERVICE,'contract_version':CONTRACT_VERSION,'stage':'dashboard','capabilities':{'health':True,'ingestion':True,'features':True,'detector':True,'evaluation':True,'dashboard':True,'agent':False}}
    @app.get('/api/v1/evaluations/{build_id}/summary')
    def evaluated_summary(build_id:str):return reader().summary(build_id)
    @app.get('/api/v1/evaluations/{build_id}/series')
    def evaluated_series(build_id:str,channel:int=Query(1,ge=1,le=4),partition:Literal['all','reference','development','evaluation']='all',
        start:int=Query(1,ge=1,le=984),stop:int=Query(984,ge=1,le=984),limit:int=Query(1000,ge=1,le=1000),offset:int=Query(0,ge=0,le=10000)):
        check_range(start,stop);return reader().series(build_id,channel,partition,start,stop,limit,offset)
    @app.get('/api/v1/evaluations/{build_id}/alerts')
    def evaluated_alerts(build_id:str,channel:int=Query(1,ge=1,le=4),partition:Literal['all','development','evaluation','reference']='all',
        start:int=Query(1,ge=1,le=984),stop:int=Query(984,ge=1,le=984),limit:int=Query(100,ge=1,le=200),offset:int=Query(0,ge=0,le=10000)):
        check_range(start,stop);return reader().alerts(build_id,channel,partition,start,stop,limit,offset)
    @app.get('/api/v1/evaluations/{build_id}/alerts/{alert_id}/evidence')
    def evaluated_evidence(build_id:str,alert_id:str,limit:int=Query(100,ge=1,le=200),offset:int=Query(0,ge=0,le=10000)):
        return reader().evidence(build_id,alert_id,limit,offset)
    @app.get('/api/v1/evaluations/{build_id}/findings')
    def evaluated_findings(build_id:str,channel:int=Query(1,ge=1,le=4),partition:Literal['all','reference','development','evaluation']='all',
        start:int=Query(1,ge=1,le=984),stop:int=Query(984,ge=1,le=984),limit:int=Query(100,ge=1,le=200),offset:int=Query(0,ge=0,le=10000)):
        check_range(start,stop);return reader().findings(build_id,channel,partition,start,stop,limit,offset)
    @app.get('/api/v1/evaluations/{build_id}/recordings/{recording_id}')
    def evaluated_recording(build_id:str,recording_id:str,channel:int=Query(1,ge=1,le=4)):
        return reader().recording(build_id,recording_id,channel)
    @app.get('/api/v1/evaluations/{build_id}/recordings/{recording_id}/signal')
    def evaluated_signal(build_id:str,recording_id:str,channel:int=Query(1,ge=1,le=4),max_points:int=Query(1024,ge=128,le=2048)):
        return reader().recording(build_id,recording_id,channel,max_points)
    @app.get('/api/v1/evaluations')
    def evaluations(limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0,le=10000)):
        with engine.connect() as connection:
            rows=connection.execute(text('SELECT build_id,plan_id,manifest_sha256,manifest,metrics FROM evaluation_builds ORDER BY build_id LIMIT :limit OFFSET :offset'),dict(limit=limit,offset=offset)).mappings().all()
        return {'stage':'CHRONOLOGICAL_EVALUATION','limit':limit,'offset':offset,'items':[dict(row) for row in rows]}
    @app.get('/api/v1/health-builds')
    def health_builds(limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0,le=10000)):
        with engine.connect() as connection:
            rows=connection.execute(text('SELECT build_id,feature_build_id,manifest_sha256,manifest FROM health_builds ORDER BY build_id LIMIT :limit OFFSET :offset'),dict(limit=limit,offset=offset)).mappings().all()
        return {'stage':'DEVELOPMENT_HEALTH','evaluation_status':'NOT_EVALUATED','limit':limit,'offset':offset,'items':[dict(row) for row in rows]}
    @app.get('/api/v1/feature-builds')
    def feature_builds(limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0,le=10000)):
        with engine.connect() as connection:
            rows=connection.execute(text('SELECT build_id,snapshot_sha256,manifest_sha256,manifest FROM feature_builds ORDER BY build_id LIMIT :limit OFFSET :offset'),dict(limit=limit,offset=offset)).mappings().all()
        return {'stage':'FEATURES_ONLY','limit':limit,'offset':offset,'items':[dict(row) for row in rows]}
    @app.get('/api/v1/recordings')
    def recordings(limit:int=Query(100,ge=1,le=200),offset:int=Query(0,ge=0,le=10000)):
        with engine.connect() as connection:
            rows=connection.execute(text('''SELECT recording_id,source_key,source_time,ordinal,expected_sha256,
                accepted_receipt_id, CASE WHEN accepted_receipt_id IS NULL THEN 'UNAVAILABLE' ELSE 'ACCEPTED' END AS admission
                FROM recordings ORDER BY ordinal LIMIT :limit OFFSET :offset'''),dict(limit=limit,offset=offset)).mappings().all()
        return {'dataset_release':'ims-nasa-archive-21001ac266c4','experiment':'ims-test-2',
                'source_timezone':'UNSPECIFIED','amplitude_unit':'native_numeric','physical_unit_verified':False,
                'integrity_scope':'admission catalog; run replay to reverify preserved bytes',
                'limit':limit,'offset':offset,'items':[dict(row) for row in rows]}
    @app.get('/api/v1/findings')
    def findings(limit:int=Query(100,ge=1,le=200),offset:int=Query(0,ge=0,le=10000)):
        with engine.connect() as connection:
            rows=connection.execute(text('''SELECT receipt_id,recording_id,admission_status,evidence
                FROM recording_receipts WHERE admission_status <> 'ACCEPTED' OR evidence->'channel_findings' <> '[]'::jsonb
                ORDER BY recording_id,receipt_id LIMIT :limit OFFSET :offset'''),dict(limit=limit,offset=offset)).mappings().all()
        return {'dataset_release':'ims-nasa-archive-21001ac266c4','limit':limit,'offset':offset,'items':[dict(row) for row in rows]}
    return app
