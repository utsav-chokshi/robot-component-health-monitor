"""Bounded reads from one registered, checksum-verified evaluation at a time."""
from contextlib import contextmanager
import hashlib,json,math
from pathlib import Path
import re
from threading import BoundedSemaphore
from uuid import UUID
import duckdb
from fastapi import HTTPException
from sqlalchemy import text
from component_health.ingestion import canonical,sha,validate_evidence,load_manifest,bounded_read

TABLES={'recordings','features','scores','alerts','alert_evidence','findings','coverage'}


def uuid_id(value):
    try:return str(UUID(value))
    except (ValueError,TypeError,AttributeError):raise HTTPException(422,'Invalid entity ID') from None


class Reader:
    def __init__(self,engine,raw,analytics):
        self.engine=engine;self.raw=Path(raw);self.analytics=Path(analytics);self.guard=BoundedSemaphore(2)

    @contextmanager
    def session(self,build_id):
        if not re.fullmatch('[0-9a-f]{64}',build_id):raise HTTPException(422,'Invalid build ID')
        if not self.guard.acquire(timeout=5):raise HTTPException(503,'Evidence reader is busy; retry shortly')
        db=None
        try:
            with self.engine.connect() as connection:
                row=connection.execute(text('''SELECT b.manifest_sha256,b.manifest,b.metrics,p.document AS plan,
                    h.manifest AS development_manifest FROM evaluation_builds b JOIN evaluation_plans p USING(plan_id)
                    JOIN health_builds h ON h.build_id=p.health_build_id WHERE b.build_id=:id'''),{'id':build_id}).mappings().first()
            if row is None:raise HTTPException(404,'Evaluated build not found')
            root=self.analytics/'evaluation-builds'/build_id
            manifest=json.loads((root/'manifest.json').read_bytes());plan=row['plan']
            if manifest!=row['manifest'] or sha(canonical(manifest))!=row['manifest_sha256'] or manifest['build_id']!=build_id or manifest['plan_id']!=build_id:
                raise ValueError('Manifest mismatch')
            if sha(canonical(plan))!=build_id or set(manifest['tables'])!=TABLES:raise ValueError('Plan/table mismatch')
            if json.loads((self.analytics/'evaluation-plans'/(build_id+'.json')).read_bytes())!=plan:raise ValueError('Plan evidence mismatch')
            for name in TABLES:
                path=root/(name+'.parquet')
                with path.open('rb') as source:digest=hashlib.file_digest(source,'sha256').hexdigest()
                if digest!=manifest['tables'][name]['file_sha256']:raise ValueError('Table checksum mismatch')
            for name,expected in [('baselines.json',plan['baselines_sha256']),('metrics.json',manifest['metrics_sha256']),('receipt-snapshot.json',plan['receipt_snapshot_sha256'])]:
                if (root/name).stat().st_size>16*1024*1024:raise ValueError('Oversize metadata')
                if sha((root/name).read_bytes())!=expected:raise ValueError('Sidecar checksum mismatch')
            metrics=json.loads((root/'metrics.json').read_bytes())
            if metrics!=row['metrics'] or sha(canonical(row['development_manifest']))!=plan['development_manifest_sha256']:raise ValueError('Registry projection mismatch')
            db=duckdb.connect(':memory:',config={'threads':1,'memory_limit':'128MB'})
            for name in TABLES:db.from_parquet(str(root/(name+'.parquet'))).create_view(name)
            yield db,dict(build_id=build_id,manifest=manifest,plan=plan,metrics=metrics,
                baselines=json.loads((root/'baselines.json').read_bytes()),policy=row['development_manifest']['policy'],
                integrity=dict(status='VERIFIED',scope='Selected evaluation artifacts; individual raw recordings checked on inspection'),
                source_timezone='UNSPECIFIED',amplitude_unit='native_numeric',physical_unit_verified=False)
        except HTTPException:raise
        except FileNotFoundError:raise HTTPException(503,'Published evidence is unavailable') from None
        except (ValueError,json.JSONDecodeError):raise HTTPException(409,'Published evidence verification failed') from None
        except Exception:raise HTTPException(503,'Evidence reader is unavailable') from None
        finally:
            if db is not None:db.close()
            self.guard.release()

    @staticmethod
    def query(db,sql,parameters):
        result=db.execute(sql,parameters);names=[column[0] for column in result.description]
        return [dict(zip(names,row)) for row in result.fetchall()]

    def summary(self,build_id):
        with self.session(build_id) as (_,context):
            context['baselines']=[dict(**{key:value for key,value in row.items() if key not in {'used','excluded'}},
                used_reference_slots=len(row['used']),excluded_reference_slots=len(row['excluded'])) for row in context['baselines']]
            return context

    def series(self,build_id,channel,partition,start,stop,limit,offset):
        with self.session(build_id) as (db,_):
            where='c.channel_index=? AND c.ordinal BETWEEN ? AND ?';args=[channel,start,stop]
            if partition!='all':where+=' AND c.partition=?';args.append(partition)
            total=db.execute('SELECT count(*) FROM coverage c WHERE '+where,args).fetchone()[0]
            rows=self.query(db,'''SELECT c.*,r.source_key,r.time_basis,r.accepted_receipt_id,f.rms,f.std_population,f.absolute_peak,f.crest_factor,
                f.quality_status,s.value AS score_value,s.baseline_id,s.direction,s.entry_threshold,s.clear_threshold
                FROM coverage c JOIN recordings r USING(recording_id) LEFT JOIN features f USING(feature_id)
                LEFT JOIN scores s ON c.score_id=s.score_id WHERE '''+where+' ORDER BY c.ordinal LIMIT ? OFFSET ?',args+[limit,offset])
            return dict(build_id=build_id,total=total,limit=limit,offset=offset,items=rows)

    def alerts(self,build_id,channel,partition,start,stop,limit,offset):
        with self.session(build_id) as (db,_):
            where='a.channel_index=?';args=[channel]
            if partition!='all':where+=' AND a.partition=?';args.append(partition)
            where+=''' AND (EXISTS(SELECT 1 FROM coverage c WHERE c.active_alert_id=a.alert_id AND c.ordinal BETWEEN ? AND ?)
                OR EXISTS(SELECT 1 FROM alert_evidence e JOIN coverage c ON c.slot_id=e.window_id WHERE e.alert_id=a.alert_id AND c.ordinal BETWEEN ? AND ?))'''
            args += [start,stop,start,stop]
            total=db.execute('SELECT count(*) FROM alerts a WHERE '+where,args).fetchone()[0]
            items=self.query(db,'SELECT a.* FROM alerts a WHERE '+where+' ORDER BY activation_time,alert_id LIMIT ? OFFSET ?',args+[limit,offset])
            return dict(build_id=build_id,total=total,limit=limit,offset=offset,items=items)

    def evidence(self,build_id,alert_id,limit,offset):
        alert_id=uuid_id(alert_id)
        with self.session(build_id) as (db,_):
            alerts=self.query(db,'SELECT * FROM alerts WHERE alert_id=?',[alert_id])
            if not alerts:raise HTTPException(404,'Alert not found in selected build')
            total=db.execute('SELECT count(*) FROM alert_evidence WHERE alert_id=?',[alert_id]).fetchone()[0]
            items=self.query(db,'''SELECT e.*,c.ordinal,c.recording_id,s.source_time,s.value AS score_value,s.status AS score_status,
                s.feature_id,s.baseline_id,s.reason_codes,f.rms,f.source_sha256 FROM alert_evidence e JOIN scores s USING(score_id)
                JOIN coverage c ON c.slot_id=e.window_id LEFT JOIN features f ON f.feature_id=s.feature_id
                WHERE e.alert_id=? ORDER BY c.ordinal,e.role LIMIT ? OFFSET ?''',[alert_id,limit,offset])
            return dict(build_id=build_id,alert=alerts[0],total=total,limit=limit,offset=offset,items=items)

    def findings(self,build_id,channel,partition,start,stop,limit,offset):
        with self.session(build_id) as (db,_):
            where='(f.channel_index IS NULL OR f.channel_index=?) AND r.ordinal BETWEEN ? AND ?';args=[channel,start,stop]
            if partition!='all':where+=' AND r.partition=?';args.append(partition)
            base=' FROM findings f JOIN recordings r USING(recording_id) WHERE '+where
            total=db.execute('SELECT count(*)'+base,args).fetchone()[0]
            items=self.query(db,'SELECT f.*,r.ordinal,r.source_time,r.partition'+base+' ORDER BY r.ordinal,f.finding_id LIMIT ? OFFSET ?',args+[limit,offset])
            return dict(build_id=build_id,total=total,limit=limit,offset=offset,items=items)

    def recording(self,build_id,recording_id,channel,signal_points=None):
        recording_id=uuid_id(recording_id)
        with self.session(build_id) as (db,context):
            records=self.query(db,'SELECT * FROM recordings WHERE recording_id=?',[recording_id])
            if not records:raise HTTPException(404,'Recording not found in selected build')
            record=records[0];features_rows=self.query(db,'SELECT * FROM features WHERE recording_id=? AND channel_index=?',[recording_id,channel])
            coverage=self.query(db,'SELECT * FROM coverage WHERE recording_id=? AND channel_index=?',[recording_id,channel])
            scores=self.query(db,'SELECT s.* FROM scores s JOIN coverage c ON c.slot_id=s.window_id WHERE c.recording_id=? AND c.channel_index=?',[recording_id,channel])
            baseline=next(row for row in context['baselines'] if row['channel_index']==channel)
            result=dict(build_id=build_id,recording=record,features=features_rows,coverage=coverage,scores=scores,
                baseline={key:value for key,value in baseline.items() if key not in {'used','excluded'}},raw_integrity='UNAVAILABLE',receipt=None)
            if record['accepted_receipt_id'] is None:
                if signal_points is not None:raise HTTPException(409,'No admitted signal for this recording')
                return result
            receipt_id=uuid_id(record['accepted_receipt_id'])
            try:
                envelope=json.loads((self.raw/'receipts'/(receipt_id+'.json')).read_bytes())
                inventory=load_manifest(Path('/app/contracts/v1/source-file-inventory.json'))
                row=next(row for row in inventory if '2nd_test/'+row['path']==record['source_key'])
                validate_evidence(envelope,row,self.raw)
                receipt=envelope['receipt']
                if receipt['receipt_id']!=receipt_id or receipt['recording_id']!=recording_id or receipt['source_sha256']!=record['source_sha256'] or receipt['admission_status']!='ACCEPTED':raise ValueError('Recording/receipt mismatch')
                result.update(raw_integrity='VERIFIED',receipt=envelope)
                if signal_points is not None:
                    data,_,reasons=bounded_read(self.raw/'objects',record['source_sha256'])
                    if reasons or sha(data)!=record['source_sha256']:raise ValueError('Signal bytes changed')
                    values=[float(line.split()[channel-1]) for line in data.decode('ascii').splitlines()]
                    bucket=math.ceil(len(values)/(signal_points//2));preview=[]
                    for start in range(0,len(values),bucket):
                        positions=range(start,min(start+bucket,len(values)))
                        extrema={min(positions,key=lambda index:values[index]),max(positions,key=lambda index:values[index])}
                        preview.extend(dict(sample_index=index,amplitude=values[index]) for index in sorted(extrema))
                    result['signal']=dict(samples=len(values),preview_method='min_max_buckets',bucket_size=bucket,points=preview,
                        amplitude_unit='native_numeric',nominal_sample_rate_hz=20000,timing_uncertainty=True)
                return result
            except FileNotFoundError:raise HTTPException(503,'Raw recording evidence is unavailable') from None
            except Exception:raise HTTPException(409,'Raw recording verification failed') from None
