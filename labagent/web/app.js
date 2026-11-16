/* 无构建工具的 React 工作台，React 依赖随项目本地提供。 */
'use strict';
const {useState, useEffect} = React;
const h = React.createElement;
const STATUS = {queued:'等待执行', running:'运行中', pause_requested:'正在暂停', paused:'已暂停',
  cancel_requested:'正在停止', cancelled:'已停止', succeeded:'已达标', exhausted:'预算结束',
  failed:'执行失败', needs_attention:'需要核对'};
const PHASE = {initialize:'配置设备', scan:'扫描测点', analyze:'分析风场', adjust:'反馈决策', apply:'应用修正'};
const NAMES = {set_fan_field:'设置风扇风场', set_flap:'设置翼板', scan_field:'扫描热线测点',
  move_probe:'移动探针', measure_hotwire:'热线采集', analyze_field:'分析风场',
  propose_adjustment:'生成控制修正', finish:'结束实验'};
const TERMINAL = ['succeeded','failed','exhausted','cancelled'];
const number = (value, digits=3) => typeof value === 'number' ? value.toFixed(digits) : '—';
const pretty = value => JSON.stringify(value, null, 2);
async function api(url, options={}) {
  const response = await fetch(url, {...options, headers:{'Content-Type':'application/json', ...options.headers}});
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}
function Button({children, onClick, disabled, kind='', type='button'}) {
  return h('button', {className:`button ${kind}`, onClick, disabled, type}, children);
}
function JsonDetail({title, value, open=false}) {
  return h('details', {className:'json-detail', open}, h('summary',null,title), h('pre',null,pretty(value)));
}
function Field({label, value, onChange, type='text', step, min}) {
  return h('label',{className:'field'},h('span',null,label),h('input',{type,value:value ?? '',step,min,
    onChange:event => onChange(type === 'number' ? Number(event.target.value) : event.target.value)}));
}
function App() {
  const [config,setConfig] = useState(null);
  const [tasks,setTasks] = useState([]);
  const [selected,setSelected] = useState(null);
  const [task,setTask] = useState(null);
  const [events,setEvents] = useState([]);
  const [tab,setTab] = useState('experiment');
  const [error,setError] = useState('');
  const [busy,setBusy] = useState(false);
  const [connected,setConnected] = useState(false);
  const [advanced,setAdvanced] = useState('');
  const [debugTool,setDebugTool] = useState('set_fan_field');
  const [debugArgs,setDebugArgs] = useState('{"spec":[{"type":"uniform","value":4}]}');
  const [debugResult,setDebugResult] = useState(null);
  const [memories,setMemories] = useState([]);
  const [query,setQuery] = useState('');
  const [requestText,setRequestText] = useState('');
  const [attachments,setAttachments] = useState([]);
  const [inputModel,setInputModel] = useState('auto');
  const [inputModels,setInputModels] = useState(null);
  const [matrixOrigin,setMatrixOrigin] = useState('bottom');
  const [parsedRequest,setParsedRequest] = useState(null);
  const [parsedConfig,setParsedConfig] = useState('');
  const [appliedRequest,setAppliedRequest] = useState(false);

  async function refresh() {
    const list = await api('/api/tasks');
    setTasks(list);
    if (selected) setTask(list.find(item => item.id === selected) || null);
  }
  useEffect(() => {
    api('/api/example').then(setConfig).catch(e=>setError(e.message));
    api('/api/input-models').then(setInputModels).catch(e=>setError(e.message));
  },[]);
  useEffect(() => {
    let alive=true;
    const poll=async()=>{
      try {
        const list=await api('/api/tasks');
        if (alive) {
          setTasks(list);
          if (selected) setTask(list.find(item=>item.id===selected) || null);
        }
      } catch (e) { if (alive) setError(e.message); }
    };
    poll();
    const timer=setInterval(poll,1000);
    return ()=>{alive=false;clearInterval(timer);};
  },[selected]);
  useEffect(() => {
    setEvents([]);
    if (!selected) return;
    const source=new EventSource(`/api/tasks/${selected}/stream`);
    source.onopen=()=>setConnected(true);
    source.onerror=()=>setConnected(false);
    source.onmessage=event=>{
      const record=JSON.parse(event.data);
      setEvents(old=>old.some(item=>item.seq===record.seq) ? old : [...old,record].slice(-2000));
    };
    return ()=>{source.close();setConnected(false);};
  },[selected]);

  async function perform(action) {
    setBusy(true);setError('');
    try { await action(); } catch(e) {setError(e.message);}
    finally {setBusy(false);}
  }
  function change(key,value) { setConfig(old=>({...old,[key]:value})); }
  function changePoint(index,key,value) {
    change('points',config.points.map((point,i)=>i===index ? {...point,[key]:value} : point));
  }
  async function start(event) {
    event.preventDefault();
    await perform(async()=>{
      const created=await api('/api/tasks',{method:'POST',body:pretty(config)});
      setSelected(created.id);setTask(created);setTab('experiment');await refresh();
    });
  }
  async function control(action) {
    await perform(async()=>{
      const updated=await api(`/api/tasks/${selected}/control/${action}`,{method:'POST'});
      setTask(updated);await refresh();
    });
  }
  async function searchMemories() {
    await perform(async()=>{
      const params=new URLSearchParams({project_id:config.project_id,device_id:config.device_id,
        calibration_version:config.calibration_version,query});
      setMemories(await api('/api/memories?'+params));
    });
  }
  function invalidateRequest() { setParsedRequest(null);setAppliedRequest(false); }
  async function selectAttachments(event) {
    const files=Array.from(event.target.files || []);
    event.target.value='';
    await perform(async()=>{
      if (files.length>5 || files.some(file=>file.size>8*1024*1024) || files.reduce((sum,file)=>sum+file.size,0)>16*1024*1024)
        throw new Error('最多 5 个附件，每个最多 8 MB，合计最多 16 MB');
      const loaded=await Promise.all(files.map(file=>new Promise((resolve,reject)=>{
        const reader=new FileReader();
        reader.onload=()=>resolve({name:file.name,data_base64:String(reader.result).split(',')[1],
          preview:file.type.startsWith('image/')?String(reader.result):null});
        reader.onerror=()=>reject(new Error(`无法读取 ${file.name}`));
        reader.readAsDataURL(file);
      })));
      setAttachments(loaded);invalidateRequest();
    });
  }
  async function parseInput() {
    await perform(async()=>{
      setParsedRequest(null);setAppliedRequest(false);
      const result=await api('/api/requests/parse',{method:'POST',body:pretty({config,text:requestText,
        attachments:attachments.map(({name,data_base64})=>({name,data_base64})),model:inputModel,matrix_origin:matrixOrigin})});
      setParsedRequest(result);setParsedConfig(pretty(config));
    });
  }
  function requestEditor() {
    const stale=parsedRequest && parsedConfig!==pretty(config) && !appliedRequest;
    return h('div',{className:'request-editor'},
      h('h3',null,'多模态风场请求'),
      h('label',{className:'field'},h('span',null,'文字描述 · 可配合表格或图片'),
        h('textarea',{rows:3,value:requestText,disabled:busy,placeholder:'例如：参考图片的剖面，底部 2 m/s，顶部 8 m/s。',
          onChange:e=>{setRequestText(e.target.value);invalidateRequest();}})),
      h('label',{className:'field'},h('span',null,'上传表格或图片'),
        h('input',{type:'file',multiple:true,disabled:busy,accept:'.xlsx,.xls,.csv,.tsv,.png,.jpg,.jpeg,.webp,.gif',onChange:selectAttachments})),
      h('p',{className:'muted'},'支持 Excel / CSV / TSV 与图片，最多 5 个，每个 8 MB。纯数值矩阵须与阵列行列数一致，单位 m/s。'),
      attachments.length>0 && h('ul',{className:'attachment-list'},attachments.map((file,index)=>
        h('li',{key:index},file.preview && h('img',{src:file.preview,alt:file.name}),h('span',null,file.name),
          h(Button,{disabled:busy,onClick:()=>{setAttachments(old=>old.filter((_,i)=>i!==index));invalidateRequest();}},'移除')))),
      h('div',{className:'form-grid'},
        h('label',{className:'field'},h('span',null,'解析模型'),h('select',{value:inputModel,disabled:busy,
          onChange:e=>{setInputModel(e.target.value);invalidateRequest();}},
          h('option',{value:'auto'},'自动 · 图片使用视觉模型'),h('option',{value:'text'},'文本模型 · 文字与表格'),
          h('option',{value:'vision'},'视觉模型'))),
        h('label',{className:'field'},h('span',null,'矩阵第一行对应位置'),h('select',{value:matrixOrigin,disabled:busy,
          onChange:e=>{setMatrixOrigin(e.target.value);invalidateRequest();}},
          h('option',{value:'bottom'},'底部 · y = 0'),h('option',{value:'top'},'顶部 · 图片阅读顺序')))),
      inputModels && h('p',{className:'muted'},`文本 ${inputModels.text.model} · 视觉 ${inputModels.vision.model}`),
      h(Button,{kind:'primary',disabled:busy || (!requestText.trim() && !attachments.length),onClick:parseInput},busy?'处理中…':'解析并预览风场'),
      parsedRequest && h('div',{className:'request-preview'},
        h('p',null,parsedRequest.summary),h('p',{className:'muted'},`解析来源 ${parsedRequest.model}`),
        parsedRequest.warnings.length>0 && h('ul',null,parsedRequest.warnings.map((warning,i)=>h('li',{key:i},warning))),
        fieldPreview(parsedRequest.field_mps),
        h(JsonDetail,{title:'生成的原语 JSON',value:parsedRequest.spec}),
        stale && h('p',{role:'status'},'实验配置已变化，请重新解析以更新预览。'),
        h(Button,{disabled:busy || stale || appliedRequest,onClick:()=>{
          setConfig(parsedRequest.config);setAdvanced(pretty(parsedRequest.config));setAppliedRequest(true);
        }},appliedRequest?'已应用到实验配置':'应用到实验配置')));
  }
  if (!config) return h('main',{className:'loading'},error || '正在载入本地实验平台…');
  const state=task?.state || {};
  const analysis=state.analysis;
  const latestScan=[...events].reverse().find(item=>item.kind==='scan_progress');
  const decisions=events.filter(item=>item.kind==='decision');
  const latestDecision=decisions.at(-1);
  const analyses=events.filter(item=>item.kind==='analysis');
  const liveTask=tasks.some(item=>!TERMINAL.includes(item.status));

  function configuration() {
    return h('section',{className:'card configuration'},
      h('div',{className:'section-heading'},h('div',null,h('small',null,'EXPERIMENT DESIGN'),h('h2',null,'配置目标风场')),
        h('span',{className:'badge'},'新实验')),
      h('form',{onSubmit:start},
        requestEditor(),
        h(Field,{label:'实验名称',value:config.name,onChange:v=>change('name',v)}),
        h('label',{className:'field'},h('span',null,'实验介绍与已有进展'),
          h('textarea',{rows:2,value:config.description,onChange:e=>change('description',e.target.value)})),
        h('div',{className:'form-grid'},
          h('label',{className:'field'},h('span',null,'决策模式'),h('select',{value:config.mode,onChange:e=>change('mode',e.target.value)},
            h('option',{value:'baseline'},'离线反馈基线'),h('option',{value:'deepseek'},'文本模型 ReAct'))),
          h(Field,{label:'风速允许误差 · m/s',type:'number',step:0.01,min:0.01,value:config.tolerance_mps,onChange:v=>change('tolerance_mps',v)}),
          h(Field,{label:'最大轮数',type:'number',min:1,value:config.max_iterations,onChange:v=>change('max_iterations',v)}),
          h(Field,{label:'稳定等待 · s',type:'number',step:0.1,min:0,value:config.settle_seconds,onChange:v=>change('settle_seconds',v)})),
        h('div',{className:'subheading'},h('h3',null,'测点计划'),h('span',null,'位置 mm · 风速 m/s · 风扇索引从 0 起')),
        h('div',{className:'table-scroll'},h('table',{className:'points-editor'},
          h('thead',null,h('tr',null,['编号','X mm','Y mm','风扇列','风扇行','目标风速',''].map((label,i)=>h('th',{key:i},label)))),
          h('tbody',null,config.points.map((point,index)=>h('tr',{key:index},
            ['id','x_mm','y_mm','fan_x','fan_y','target_mps'].map(key=>h('td',{key},h('input',{
              'aria-label':`${point.id} ${key}`,type:key==='id'?'text':'number',step:key==='target_mps'?0.1:1,
              value:point[key],onChange:e=>changePoint(index,key,key==='id'?e.target.value:Number(e.target.value))}))),
            h('td',null,h('button',{type:'button',className:'remove',disabled:config.points.length<=1,
              onClick:()=>change('points',config.points.filter((_,i)=>i!==index))},'×'))))))),
        h(Button,{onClick:()=>change('points',[...config.points,{id:`p${Date.now().toString().slice(-6)}`,
          x_mm:0,y_mm:100,fan_x:0,fan_y:0,target_mps:4}])},'＋ 添加测点'),
        h('details',{className:'advanced'},h('summary',null,'高级参数 · 翼板、采样、标定与原语'),
          h('p',{className:'muted'},'编辑完整 JSON 后点击应用。target_ti 为湍流度比例，例如 0.1。'),
          h(Button,{onClick:()=>setAdvanced(pretty(config))},'载入当前配置'),
          h('textarea',{className:'code-editor',rows:14,value:advanced,onChange:e=>setAdvanced(e.target.value),placeholder:'点击载入当前配置'}),
          h(Button,{onClick:()=>perform(async()=>{setConfig(await api('/api/validate',{method:'POST',body:pretty(JSON.parse(advanced))}));})},'应用 JSON')),
        h('div',{className:'form-footer'},h('span',{className:'muted'},'默认模拟设备 · 实验数据保存在本机'),
          h(Button,{type:'submit',kind:'primary',disabled:busy || liveTask},busy?'处理中…':'启动实验 →'))));
  }

  function experiment() {
    return h(React.Fragment,null,configuration(),
      h('section',{className:'card run-panel'},
        h('div',{className:'section-heading'},h('div',null,h('small',null,'LIVE EXPERIMENT'),h('h2',null,task?.spec.name || '实验运行面板')),
          task && h('span',{className:`badge ${task.status}`},STATUS[task.status] || task.status)),
        !task ? h('div',{className:'empty'},h('div',{className:'wind-mark'},'≈'),h('h3',null,'从一个可测量的目标开始'),
          h('p',null,'启动左侧实验，或从历史记录选择任务。'),h('p',{className:'muted'},'设置 → 扫描 → 分析 → 调整')) :
        h(React.Fragment,null,
          h('p',{className:'muted'},task.spec.description),
          h('div',{className:'metrics'},
            metric('当前阶段',PHASE[state.phase] || '准备中'),
            metric('实验轮次',`${state.iteration || 0} / ${task.spec.max_iterations}`),
            metric('最大风速误差',`${number(analysis?.max_error_mps)} m/s`),
            metric('已运行时间',`${number(task.elapsed_seconds,1)} s`)),
          h('div',{className:'progress-label'},h('span',null,'最近扫描进度'),
            h('span',null,latestScan ? `${latestScan.payload.completed} / ${latestScan.payload.total} 个测点` : '等待采集')),
          h('progress',{max:latestScan?.payload.total || 1,value:latestScan?.payload.completed || 0}),
          h('div',{className:'toolbar'},
            ['running','queued'].includes(task.status) && h(Button,{onClick:()=>control('pause'),disabled:busy},'暂停'),
            ['paused','needs_attention'].includes(task.status) && h(Button,{onClick:()=>control('resume'),disabled:busy,kind:'primary'},'核对后继续'),
            !TERMINAL.includes(task.status) && h(Button,{onClick:()=>control('cancel'),disabled:busy || task.status==='cancel_requested',kind:'danger'},'请求停止'),
            h('a',{className:'button',href:`/api/tasks/${task.id}/download`},'下载实验包'),
            h('a',{className:'button',href:`/api/tasks/${task.id}/logs`},'下载日志')),
          h('p',{className:'hint'},'暂停在工具边界生效并保持当前设置。停止需等待设备确认。'),
          task.error && h('div',{className:'error-box'},task.error),
          state.stop_reason && h('p',{className:'hint'},state.stop_reason),
          h('div',{className:'decision-card'},h('small',null,'最新决策摘要'),
            h('p',null,latestDecision?.payload.summary || '等待下一条决策…')),
          analysis && h('div',{className:'results'},h('h3',null,'目标与实测'),
            h('img',{className:'field-chart',src:`/api/tasks/${task.id}/artifacts/${analysis.plot_ref}`,alt:'各测点目标与实测风速对比'}),
            resultTable(analysis.points),errorChart(analyses)),
          h('h3',null,'执行记录'),
          h('div',{className:'timeline'},[...events].reverse().slice(0,160).map(event=>eventView(event))),
          h(JsonDetail,{title:'当前实验状态与上下文',value:state}))));
  }
  function toolsPanel() {
    return h('section',{className:'card'},h('small',null,'TOOLS / LAYERED'),h('h2',null,'工具与调试'),
      h('p',{className:'muted'},'设备动作、分析和控制策略分开组织。独立调试使用模拟器，返回结果可直接展开。'),
      h('div',{className:'tool-groups'},[
        ['设备层','set_fan_field / set_flap / scan_field','风扇、翼板与 XY 热线扫描'],
        ['分析层','analyze_field','统计、湍流度、误差与图表'],
        ['控制层','propose_adjustment','有界反馈策略，后续替换优化算法']
      ].map(([title,code,description])=>h('article',{className:'tool-group',key:title},h('h3',null,title),h('code',null,code),h('p',null,description)))),
      h('label',{className:'field'},h('span',null,'选择模拟调试工具'),h('select',{value:debugTool,onChange:e=>{
        setDebugTool(e.target.value);setDebugArgs(e.target.value==='set_fan_field'?'{"spec":[{"type":"uniform","value":4}]}':e.target.value==='set_flap'?'{"angle_deg":0,"amplitude_deg":5,"frequency_hz":1}':'{}');
      }},['set_fan_field','set_flap','scan_field'].map(name=>h('option',{key:name,value:name},NAMES[name])))),
      h('textarea',{className:'code-editor',rows:8,value:debugArgs,onChange:e=>setDebugArgs(e.target.value)}),
      h(Button,{kind:'primary',disabled:busy || liveTask,onClick:()=>perform(async()=>{
        setDebugResult(await api('/api/debug',{method:'POST',body:pretty({spec:config,tool:debugTool,arguments:JSON.parse(debugArgs)})}));
      })},'运行模拟调试'),
      debugResult && h(JsonDetail,{title:'工具返回结果',value:debugResult,open:true}));
  }
  function memoryPanel() {
    return h('section',{className:'card'},h('small',null,'LONG-TERM MEMORY'),h('h2',null,'实验记录与经验'),
      h('p',{className:'muted'},'按当前配置中的项目、设备和标定版本检索。实验事实保留来源，候选经验需复核。'),
      h('div',{className:'form-grid'},h(Field,{label:'项目',value:config.project_id,onChange:v=>change('project_id',v)}),
        h(Field,{label:'标定版本',value:config.calibration_version,onChange:v=>change('calibration_version',v)})),
      h(Field,{label:'检索词，可留空',value:query,onChange:setQuery}),
      h(Button,{onClick:searchMemories,disabled:busy,kind:'primary'},'检索记忆'),
      h('div',{className:'memory-list'},memories.map(memory=>h('article',{key:memory.id,className:'memory-item'},
        h('div',{className:'subheading'},h('h3',null,memory.kind==='experiment'?'实验记录':'候选经验'),h('span',{className:'badge'},memory.status)),
        h('p',{className:'muted'},memory.task_id),
        h(JsonDetail,{title:'内容、条件与证据',value:memory.content}),
        memory.kind==='experience' && h('div',{className:'toolbar'},['verified','disputed','expired'].map(status=>h(Button,{key:status,
          onClick:()=>perform(async()=>{await api(`/api/memories/${memory.id}`,{method:'PATCH',body:pretty({status})});await searchMemories();})},
          {verified:'标记已复核',disputed:'标记有争议',expired:'标记过期'}[status])))))));
  }
  return h('div',{className:'app-shell'},
    h('aside',{className:'sidebar'},h('div',{className:'brand'},h('span',{className:'brand-icon'},'≈'),h('div',null,h('strong',null,'LabAgent'),h('small',null,'流体实验工作台'))),
      h('div',{className:'sidebar-label'},'WORKSPACE'),
      h('nav',null,[['experiment','◈','风场实验'],['tools','⊞','工具调试'],['memory','◷','长期记忆']].map(([id,icon,title])=>
        h('button',{key:id,className:tab===id?'nav-item active':'nav-item',onClick:()=>setTab(id)},h('span',null,icon),title))),
      h('div',{className:'sidebar-label'},'实验历史'),
      h('div',{className:'task-list'},tasks.map(item=>h('button',{key:item.id,className:`task-item ${selected===item.id?'selected':''}`,
        onClick:()=>{setSelected(item.id);setTask(item);setTab('experiment');}},h('strong',null,item.spec.name),
        h('span',null,STATUS[item.status] || item.status),h('small',null,new Date(item.created_at).toLocaleString('zh-CN'))))),
      h('div',{className:'sidebar-bottom'},h('span',{className:'status-dot'}),'单机 · 默认模拟设备')),
    h('main',{className:'main'},h('header',{className:'page-header'},h('div',null,h('small',null,'WIND FIELD LABORATORY'),
      h('h1',null,{experiment:'让风场，接近你的目标。',tools:'每个动作，都有清楚的边界。',memory:'让实验留下可复用的证据。'}[tab])),
      h('span',{className:'connection'},h('span',{className:`status-dot ${connected?'':'offline'}`}),selected?(connected?'事件流已连接':'事件流重连中'):'本地工作台')),
      error && h('div',{className:'error-box',role:'alert'},error,h(Button,{onClick:()=>setError('')},'关闭')),
      h('div',{className:tab==='experiment'?'workspace-grid':''},tab==='experiment'?experiment():tab==='tools'?toolsPanel():memoryPanel()),
      h('footer',null,'LabAgent · 操作记录 / 测量证据 / 可复现实验')));
}
function fieldPreview(field) {
  const rows=field.length, columns=field[0].length;
  const maximum=Math.max(0.01,...field.flat());
  return h('figure',{className:'field-preview'},
    h('div',{className:'field-grid',role:'img','aria-label':`${rows} 行 ${columns} 列目标风速热图`,
      style:{gridTemplateColumns:`repeat(${columns},1fr)`}},
      field.slice().reverse().flatMap((row,index)=>row.map((value,x)=>h('div',{
        key:`${index}-${x}`,title:`x=${x}, y=${rows-1-index} · ${number(value)} m/s`,
        style:{backgroundColor:`hsl(${205-value/maximum*170},65%,${90-value/maximum*50}%)`}
      })))),
    h('figcaption',null,`顶部 y=${rows-1} ↑ · 底部 y=0 · 左 x=0 → 右 x=${columns-1} · 0–${number(maximum,2)} m/s`));
}
function metric(label,value) {return h('div',{className:'metric',key:label},h('span',null,label),h('strong',null,value));}
function resultTable(points) {
  return h('div',{className:'table-scroll'},h('table',null,h('thead',null,h('tr',null,
    ['测点','目标 m/s','实测 m/s','误差 m/s','湍流度','结果'].map(label=>h('th',{key:label},label)))),
    h('tbody',null,points.map(point=>h('tr',{key:point.point_id},h('td',null,point.point_id),
      h('td',null,number(point.target_mps)),h('td',null,number(point.mean_mps)),h('td',null,number(point.error_mps)),
      h('td',null,point.turbulence_intensity===null?'未定义':number(point.turbulence_intensity*100,1)+'%'),
      h('td',null,h('span',{className:point.passed?'pass':'muted'},point.passed?'达标':'待调整')))))));
}
function errorChart(events) {
  if (!events.length) return null;
  const unique=[...new Map(events.map(event=>[event.payload.iteration,event.payload])).values()];
  const maximum=Math.max(0.01,...unique.map(item=>item.rmse_mps));
  const coordinates=unique.map((item,i)=>`${35+i/Math.max(1,unique.length-1)*470},${125-item.rmse_mps/maximum*95}`).join(' ');
  return h('div',{className:'error-chart'},h('h3',null,'误差随轮次变化 · RMSE m/s'),
    h('svg',{viewBox:'0 0 550 155',role:'img','aria-label':'每轮风场均方根误差'},
      h('path',{d:'M35 20V130H520',fill:'none',stroke:'#d9dfdf'}),
      h('polyline',{points:coordinates,fill:'none',stroke:'#22867b',strokeWidth:3}),
      unique.map((item,i)=>h('g',{key:item.iteration},h('circle',{cx:35+i/Math.max(1,unique.length-1)*470,
        cy:125-item.rmse_mps/maximum*95,r:4,fill:'#22867b'}),
        h('text',{x:35+i/Math.max(1,unique.length-1)*470,y:148,textAnchor:'middle',fontSize:11},item.iteration)))));
}
function eventView(event) {
  const payload=event.payload;
  const titles={decision:'决策摘要',tool_started:'开始调用',tool_completed:'工具完成',tool_error:'工具报错',
    analysis:'风场分析完成',scan_progress:'扫描进度',experiment_finished:'实验结束',paused:'已暂停',
    control_requested:'控制请求',task_created:'创建实验',experiment_error:'执行错误',needs_attention:'需核对设备结果'};
  return h('details',{key:event.seq,className:`event ${event.kind.includes('error')?'event-error':''}`},
    h('summary',null,h('time',null,new Date(event.created_at).toLocaleTimeString('zh-CN')),
      h('strong',null,titles[event.kind] || event.kind),
      h('span',{className:'event-summary'},payload.summary || payload.error || NAMES[payload.tool] || payload.point_id || payload.status || '')),
    payload.summary && h('p',null,payload.summary),h('pre',null,pretty(payload)));
}
ReactDOM.createRoot(document.getElementById('root')).render(h(App));
