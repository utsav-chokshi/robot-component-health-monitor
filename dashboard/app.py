import math
import pandas as pd
import altair as alt
import streamlit as st
from client import get,EvidenceError

st.set_page_config(page_title='Component Health Monitor',page_icon='◉',layout='wide',initial_sidebar_state='expanded')
st.markdown('''<style>
.block-container{padding-top:2rem;padding-bottom:2rem;max-width:1500px}
h1{letter-spacing:-.035em;font-weight:720!important}
[data-testid="stMetric"]{background:#fff;border:1px solid #dde5ee;border-radius:12px;padding:16px 18px}
[data-testid="stMetricValue"]{font-size:1.75rem}
.eyebrow{font-size:.73rem;font-weight:700;letter-spacing:.15em;color:#527084;margin-bottom:.55rem}
.hero-note{color:#577084;font-size:1rem;max-width:860px;margin-bottom:1.4rem}
</style>''',unsafe_allow_html=True)
st.markdown('<div class="eyebrow">IMS BEARING EXPERIMENT 02 · FROZEN EVALUATION</div>',unsafe_allow_html=True)
st.title('Component health monitor')
st.markdown('<div class="hero-note">Explore bearing signals, reference-relative scores and the recordings behind each alert.</div>',unsafe_allow_html=True)


def fetch(path,**params):
    try:return get(path,**params)
    except EvidenceError as error:
        st.error(str(error));st.button('Retry',key='retry_'+path, on_click=lambda:None);st.stop()


catalog=fetch('/api/v1/evaluations',limit=100,offset=0)['items']
if not catalog:
    st.info('No frozen evaluation is available yet. Complete the evaluation using the project reproduction guide.');st.stop()
ids=[row['build_id'] for row in catalog]
with st.sidebar:
    st.subheader('Evidence workspace')
    build=st.selectbox('Evaluated build',ids,format_func=lambda value:'IMS test 2 · '+value[:12])
    channel=st.selectbox('Component',[1,2,3,4],format_func=lambda value:f'Bearing {value} · channel {value}')
    partition_label=st.selectbox('Partition',['All captures','Evaluation','Development','Reference'])
    scope=st.slider('Capture range',1,984,(1,984),help='Capture order is fixed by the source manifest.')
    st.caption('Read-only evidence. No robot or maintenance actions.')
    st.divider()
    st.caption('Industrial bearing test rig · source timezone unspecified · amplitude units unverified.')
partition={'All captures':'all','Reference':'reference','Development':'development','Evaluation':'evaluation'}[partition_label]
base=f'/api/v1/evaluations/{build}'
summary=fetch(base+'/summary')
params=dict(channel=channel,partition=partition,start=scope[0],stop=scope[1])
series_response=fetch(base+'/series',**params,limit=1000,offset=0)
alerts_response=fetch(base+'/alerts',**params,limit=200,offset=0)
findings_response=fetch(base+'/findings',**params,limit=200,offset=0)
df=pd.DataFrame(series_response['items']);alerts=alerts_response['items']
if series_response['total']!=len(df):st.error('The scope is larger than the reader limit. Narrow the capture range.');st.stop()
st.caption('Published artifact checks passed · individual raw recordings are verified when inspected.')
if df.empty:st.info('No captures fall within this selection. Adjust the partition or capture range.');st.stop()
st.subheader(f'Bearing {channel}')
st.caption(f"{partition_label} · captures {int(df.ordinal.min())}–{int(df.ordinal.max())} · {len(df):,} expected windows · source timezone UNSPECIFIED")
operational=df[df.score_status.isin(['SCORABLE','UNSCORABLE'])]
scorable=df[df.score_status.eq('SCORABLE')]
kpis=st.columns(4)
kpis[0].metric('Score coverage',f'{len(scorable):,}/{len(operational):,}' if len(operational) else 'Fit only')
kpis[1].metric('Windows ≥ entry',f'{int((scorable.score_value>=6).sum()):,}/{len(scorable):,}' if len(scorable) else 'Not scored')
kpis[2].metric('Alert episodes',str(alerts_response['total']),help='Episodes with active or evidence windows intersecting the selected captures.')
kpis[3].metric('Open at boundary',str(sum(row['state']=='OPEN_AT_BOUNDARY' for row in alerts)))
tabs=st.tabs(['Trends','Alert evidence','Data quality','Provenance'])
colors=alt.Scale(domain=['reference','development','evaluation'],range=['#71859C','#087F8C','#D18A34'])


def chart_frame(frame):
    return alt.Chart(frame).encode(x=alt.X('ordinal:Q',title='Capture order · nominal 10-minute spacing',scale=alt.Scale(domain=[int(df.ordinal.min())-.5,int(df.ordinal.max())+.5])))


def bands():
    frame=pd.DataFrame([dict(start=.5,end=196.5,partition='reference'),dict(start=196.5,end=392.5,partition='development'),dict(start=392.5,end=984.5,partition='evaluation')])
    low=float(df.ordinal.min())-.5;high=float(df.ordinal.max())+.5
    frame=frame[(frame.end>low)&(frame.start<high)].copy()
    frame['start']=frame.start.clip(lower=low);frame['end']=frame.end.clip(upper=high)
    return alt.Chart(frame).mark_rect(opacity=.055,clip=True).encode(x=alt.X('start:Q',scale=alt.Scale(domain=[low,high])),x2='end:Q',color=alt.Color('partition:N',scale=colors,legend=None))


with tabs[0]:
    feature=st.selectbox('Feature',['RMS','Population standard deviation','Absolute peak','Crest factor'])
    field={'RMS':'rms','Population standard deviation':'std_population','Absolute peak':'absolute_peak','Crest factor':'crest_factor'}[feature]
    unit='Dimensionless ratio' if feature=='Crest factor' else 'Native amplitude · physical scaling unverified'
    measured=df[df.feature_eligible].copy()
    points=chart_frame(measured).mark_point(filled=True,size=18,opacity=.8).encode(y=alt.Y(field+':Q',title=feature,scale=alt.Scale(zero=False)),
        color=alt.Color('partition:N',scale=colors,title='Partition'),tooltip=['ordinal','expected_source_time','partition',alt.Tooltip(field+':Q',format='.6f')])
    st.altair_chart((bands()+points).properties(height=285),width='stretch')
    st.caption(unit+' · each dot is one independent recording/channel window; missing inputs are not interpolated.')
    st.markdown('**Reference-relative deviation**')
    compressed=st.checkbox('Keep small and large deviations visible',value=True,help='Compress large values while retaining zero and readable score-unit labels.')
    if not scorable.empty:
        scale=alt.Scale(type='symlog',constant=1) if compressed else alt.Scale(zero=True)
        score_points=chart_frame(scorable).mark_point(filled=True,size=20,opacity=.85).encode(y=alt.Y('score_value:Q',title='Robust deviation · dimensionless',scale=scale),
            color=alt.Color('partition:N',scale=colors,legend=None),tooltip=['ordinal','expected_source_time',alt.Tooltip('score_value:Q',format='.3f'),'observed_state'])
        limits=pd.DataFrame([dict(value=6.,rule='Entry ≥6'),dict(value=4.,rule='Clear <4')])
        rules=alt.Chart(limits).mark_rule(strokeDash=[5,4],opacity=.8).encode(y='value:Q',color=alt.Color('rule:N',scale=alt.Scale(domain=['Entry ≥6','Clear <4'],range=['#BA542F','#536887']),title='Policy'))
        st.altair_chart((bands()+score_points+rules).resolve_scale(color='independent').properties(height=285),width='stretch')
    else:st.info('Reference captures calibrate the baseline. Scores begin in development; unavailable inputs remain unscorable.')
    st.caption('Entry: two successive valid scores ≥6. Clearance: three successive valid scores <4. Gaps interrupt streaks. Reference windows are fit-only.')
    baseline=next(row for row in summary['baselines'] if row['channel_index']==channel)
    with st.expander('Reference calibration'):
        st.write(f"{baseline['used_reference_slots']}/{baseline['declared_reference_slots']} reference windows used · {baseline['status']}")
        st.dataframe(pd.DataFrame([{key:baseline[key] for key in ['location','mad','scale','amplitude_unit','reference_health']}]),hide_index=True,width='stretch')
        st.caption('Reference is a calibration assumption, not a supplied healthy label.')

with tabs[1]:
    if not alerts:st.info('No alert episode intersects this scope. This does not establish component health.')
    else:
        by_alert={row['alert_id']:row for row in alerts}
        alert_id=st.selectbox('Alert episode',list(by_alert),format_func=lambda value,choices=by_alert:choices[value]['activation_time']+' · '+choices[value]['state'].replace('_',' ').lower())
        alert=by_alert[alert_id]
        st.write(f"**{alert['state'].replace('_',' ').title()}** · {alert['partition']} · source timezone unspecified")
        st.dataframe(pd.DataFrame([{key:alert[key] for key in ['first_high_time','activation_time','latest_processed_time','closed_time','coverage_interrupted']}]),hide_index=True,width='stretch')
        first_page=fetch(base+f'/alerts/{alert_id}/evidence',limit=100,offset=0)
        page=int(st.number_input('Evidence page',min_value=1,max_value=max(1,math.ceil(first_page['total']/100)),value=1,step=1,key='evidence_page_'+alert_id))
        evidence=first_page if page==1 else fetch(base+f'/alerts/{alert_id}/evidence',limit=100,offset=(page-1)*100)
        st.caption(f"Full-episode evidence · {evidence['total']} rows · page {page} of {max(1,math.ceil(evidence['total']/100))}")
        if evidence['items']:
            evidence_df=pd.DataFrame(evidence['items'])
            st.dataframe(evidence_df[['ordinal','source_time','role','score_value','score_status','rms']],hide_index=True,width='stretch')
            options={row['recording_id']:row for row in evidence['items']}
            inspect_id=st.selectbox('Inspect evidence recording',list(options),format_func=lambda value,choices=options:f"Capture {choices[value]['ordinal']} · {choices[value]['source_time']}")
            recording=fetch(base+f'/recordings/{inspect_id}',channel=channel)
            st.success('Raw bytes and receipt verified' if recording['raw_integrity']=='VERIFIED' else 'No admitted raw recording')
            with st.expander('Evidence lineage'):
                st.json(dict(recording=recording['recording'],features=recording['features'],scores=recording['scores'],baseline=recording['baseline'],receipt=recording['receipt']))
        else:st.info('No evidence rows on this page. Choose an earlier page.')
        st.caption('An episode span describes observed captures, not continuous failure or alarm duration.')

with tabs[2]:
    quality=st.columns(3)
    quality[0].metric('Admitted captures',f"{int(df.source_admission.eq('ACCEPTED').sum())}/{len(df)}")
    quality[1].metric('Eligible features',f'{int(df.feature_eligible.sum())}/{len(df)}')
    quality[2].metric('Unscorable windows',str(int(df.score_status.eq('UNSCORABLE').sum())))
    gaps=df[(~df.feature_eligible)|df.score_status.eq('UNSCORABLE')|df.coverage_uncertain]
    if gaps.empty:st.info('No missing or unscorable windows are recorded in this scope. Physical amplitude units and source timezone remain unverified.')
    else:st.dataframe(gaps[['ordinal','expected_source_time','source_admission','reason_codes','score_reason_codes','coverage_uncertain']],hide_index=True,width='stretch')
    if findings_response['items']:st.dataframe(pd.DataFrame(findings_response['items']),hide_index=True,width='stretch')
    st.caption(f"Recorded quality findings: {findings_response['total']}. Reference fit-only windows are not failed operational scores.")
    with st.expander('Interpretation and unavailable metrics'):
        st.write('These scores describe reference-relative signal changes. Fault diagnosis and predictive performance remain unvalidated; channels 2–4 are not labeled healthy controls.')
        st.json(summary['metrics']['unsupported'])
        st.write('Duplicate delivery attempts: not measured by a durable attempt journal.')

with tabs[3]:
    options={row['recording_id']:row for row in series_response['items']}
    recording_id=st.selectbox('Source recording',list(options),index=len(options)-1,format_func=lambda value,choices=options:f"Capture {choices[value]['ordinal']} · {choices[value]['expected_source_time']}")
    if options[recording_id]['source_admission']!='ACCEPTED':
        st.info('No admitted raw signal exists for this capture. Its missing or rejected status remains in coverage.')
    else:
        raw=fetch(base+f'/recordings/{recording_id}/signal',channel=channel,max_points=1024)
        st.write(f"**Capture {options[recording_id]['ordinal']}** · {raw['recording']['source_time']} · source timezone UNSPECIFIED")
        st.success('Source recording checksum and admission receipt verified')
        preview=pd.DataFrame(raw['signal']['points'])
        waveform=alt.Chart(preview).mark_line(color='#087F8C',strokeWidth=.7).encode(x=alt.X('sample_index:Q',title='Sample index'),y=alt.Y('amplitude:Q',title='Native amplitude · unverified scaling'),tooltip=['sample_index','amplitude'])
        st.altair_chart(waveform.properties(height=240),width='stretch')
        st.caption('Min/max preview, not all samples · 20,480 samples/channel · nominal 20 kHz · one-second / nominal 1.024-second source timing discrepancy retained.')
        st.dataframe(pd.DataFrame(raw['features'])[['rms','std_population','absolute_peak','crest_factor','quality_status']],hide_index=True,width='stretch')
        with st.expander('Build and source provenance'):
            st.json(dict(evaluation_build=build,plan=summary['plan'],recording=raw['recording'],receipt=raw['receipt'],baseline=raw['baseline']))
    st.download_button('Download selected window metadata',df.assign(evaluation_build_id=build).to_csv(index=False).encode(),file_name=f'bearing-{channel}-{build[:12]}-windows.csv',mime='text/csv')

st.divider()
st.caption('Reference-relative condition monitoring · industrial test-rig case study · no maintenance actions or model inference.')
