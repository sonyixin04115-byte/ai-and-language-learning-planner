const $=s=>document.querySelector(s),esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const API='http://127.0.0.1:8001/api';
// Keep modal forms inside the visible area when an iPad keyboard opens.
function fitDialogViewport(){
 const viewport=window.visualViewport,style=document.documentElement?.style;
 if(!viewport||!style)return;
 style.setProperty('--dialog-viewport-height',Math.max(1,Math.floor(viewport.height))+'px');
 style.setProperty('--dialog-viewport-top',Math.max(0,Math.floor(viewport.offsetTop))+'px');
}
if(window.visualViewport){
 window.visualViewport.addEventListener('resize',fitDialogViewport,{passive:true});
 window.visualViewport.addEventListener('scroll',fitDialogViewport,{passive:true});
 fitDialogViewport();
}
function ds(d){return [d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-')}
const today=()=>ds(new Date());
function shift(day,n){const d=new Date(day+'T12:00:00');d.setDate(d.getDate()+n);return ds(d)}
let state=null,view='today',renderedView='today',selected=today(),registering=false,editing=null,courseEditing=null,filter='all',courseFilter='all',openedCourse=null,lessons=[],lessonQuery='',lessonLoadToken=0,refreshGeneration=0,navigationGeneration=0,pendingMutations=0;
let renderedCalendarDay=today(),dayBoundaryTimer=null;
const lessonWrites=new Set();
const taskActionWrites=new Set();
let taskMoveNotice=null;
const restTaskExpanded=new Set();
let dayRestBusy=false;
const languageCaches=new Map(),languageWrites=new Set(),languageGradeWrites=new Map();
let languageBatch=null;
let languageSettingBusy=false;
let languageOwner='',languageDays={english:today(),korean:today()},languageModes={english:'daily',korean:'daily'};
let languageHistoryPages={english:1,korean:1};
let avatarDraft=null,avatarBusy='',avatarSelection=0,avatarOwner='';
async function api(path,method='GET',body){const writing=!['GET','HEAD','OPTIONS'].includes(method);if(writing){pendingMutations++;refreshGeneration++}try{const r=await fetch(API+path,{method,credentials:'include',headers:{'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});const data=await r.json();if(!r.ok){if(r.status===401&&state){if(window.StudyListening&&typeof window.StudyListening.dispose==='function')window.StudyListening.dispose();resetAvatarDraft();languageCaches.clear();languageOwner='';state=null;$('#app').hidden=true;$('#auth').hidden=false}throw Error(data.error||'操作失败')}return data}finally{if(writing)pendingMutations--}}
function toast(message){$('#toast').textContent=message;$('#toast').style.display='block';clearTimeout(window.toastTimer);window.toastTimer=setTimeout(()=>$('#toast').style.display='none',3500)}
function quickAddDraft(){const f=$('#quickAdd');return f?{day:selected,title:f.elements.title.value,planned:f.elements.planned.value,category:f.elements.category.value}:null}
function restoreQuickAddDraft(draft,allowDateChange=false){
 const form=$('#quickAdd');
 if(!form||!draft||(!allowDateChange&&draft.day!==selected))return;
 ['title','planned','category'].forEach(key=>form.elements[key].value=draft[key]);
}
function normaliseMainDay(day=selected){
 const minimum=today();
 return /^\d{4}-\d{2}-\d{2}$/.test(day)&&day>=minimum?day:minimum;
}
function rolloverMainDay(){
 const day=normaliseMainDay();
 if(state&&(view==='today'||view==='journal')&&(day!==selected||renderedCalendarDay!==today()))render();
 else selected=day;
}
function scheduleMainDayBoundary(){
 if(dayBoundaryTimer!==null)clearTimeout(dayBoundaryTimer);
 const now=new Date(),next=new Date(now);
 next.setHours(24,0,0,0);
 dayBoundaryTimer=setTimeout(()=>{
  dayBoundaryTimer=null;
  rolloverMainDay();
  scheduleMainDayBoundary();
 },Math.max(1,next.getTime()-now.getTime()+25));
}
function quickAddDirty(){const f=$('#quickAdd');return !!f&&['title','planned','category'].some(k=>f.elements[k].value!==f.elements[k].defaultValue)}
function settingsDirty(){const f=$('#settingsForm');return !!f&&['name','minutes'].some(k=>f.elements[k].value!==f.elements[k].defaultValue)}
function canAutoRefresh(){return !!state&&document.visibilityState==='visible'&&!document.querySelector('dialog[open]')&&!pendingMutations&&!dayRestBusy&&!lessonWrites.size&&!taskActionWrites.size&&!quickAddDirty()&&!settingsDirty()&&!languageHasDirty()&&!languageWrites.size&&!languageBatch&&!avatarBusy&&avatarDraft===null&&(!window.StudyListening||typeof window.StudyListening.canAutoRefresh!=='function'||window.StudyListening.canAutoRefresh())}
async function refresh({background=false}={}){
 if(background&&!canAutoRefresh())return false;
 const generation=++refreshGeneration,courseId=openedCourse;
 const nextState=await api('/state');
 if(generation!==refreshGeneration)return false;
 let nextLessons=null,courseMissing=false;
 if(courseId&&nextState.courses.some(c=>c.id===courseId)){
  const d=await api('/courses/'+courseId+'/lessons');
  if(generation!==refreshGeneration)return false;
  Object.assign(nextState.courses.find(c=>c.id===courseId),d.course);nextLessons=d.lessons;
 }else if(courseId)courseMissing=true;
 if(generation!==refreshGeneration||(background&&!canAutoRefresh()))return false;
 const draft=quickAddDraft();
 if(avatarOwner!==nextState.user.username){resetAvatarDraft();avatarOwner=nextState.user.username}
 if(languageOwner!==nextState.user.username){languageCaches.clear();languageOwner=nextState.user.username;languageDays={english:today(),korean:today()};languageModes={english:'daily',korean:'daily'};languageHistoryPages={english:1,korean:1}}
 if(background&&isLanguageView()){const active=languageCache(false);if(active&&!languageCacheDirty(active))active.loaded=false}
 if(state&&state.user.username===nextState.user.username&&languageHasDirty())nextState.language_settings=state.language_settings;
 state=nextState;
 if(openedCourse===courseId){if(courseMissing){openedCourse=null;lessons=[]}else if(nextLessons)lessons=nextLessons}
 $('#auth').hidden=true;$('#app').hidden=false;$('#profile').textContent=state.user.name;updateHeaderAvatar();render();
 restoreQuickAddDraft(draft);
 return true;
}
function hero(title,sub,action=''){return `<div class="hero"><div><span class="eyebrow">YOUR DAY, YOUR CHOICE</span><h1>${esc(title)}</h1><p>${esc(sub)}</p></div>${action}</div>`}
function resetAvatarDraft(){avatarDraft=null;avatarBusy='';avatarSelection++;avatarOwner=''}
function avatarInitial(){return Array.from(String(state&&state.user&&(state.user.name||state.user.username)||'我').trim())[0]||'我'}
function safeAvatar(value){
 if(typeof value!=='string'||value.length>60000||!/^data:image\/jpeg;base64,(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value))return '';
 try{const binary=atob(value.slice(23));return binary.length>=4&&binary.length<=40000&&binary.charCodeAt(0)===255&&binary.charCodeAt(1)===216&&binary.charCodeAt(2)===255&&binary.charCodeAt(binary.length-2)===255&&binary.charCodeAt(binary.length-1)===217?value:''}catch{return ''}
}
function avatarHTML(value){const avatar=safeAvatar(value);return avatar?`<img src="${esc(avatar)}" alt="个人头像" width="192" height="192">`:`<span aria-hidden="true">${esc(avatarInitial())}</span>`}
function paintAvatar(element,value){if(!element)return;element.innerHTML=avatarHTML(value);const image=element.querySelector('img');if(image)image.onerror=()=>{if(image.isConnected&&element.contains(image))element.textContent=avatarInitial()}}
function updateHeaderAvatar(){paintAvatar($('#profileAvatar'),state&&state.user.avatar)}
function avatarSettingsHTML(){return `<form id="avatarForm" class="card profile-avatar-card"><h3>个人头像</h3><div class="profile-avatar-preview-row"><div id="avatarPreview" class="profile-avatar-preview" role="img" aria-label="头像预览">${avatarHTML(avatarDraft===null?state.user.avatar:avatarDraft)}</div><div><p class="profile-avatar-caption">选择图片后先预览，点击「保存头像」才会同步到账号。</p><small class="muted">支持 JPG、PNG、WebP，原图最多 10 MB。将裁剪为正方形。</small></div></div><label class="profile-avatar-picker">选择或更换图片<input id="avatarFile" name="avatar_file" type="file" accept="image/png,image/jpeg,image/webp"></label><p id="avatarStatus" class="profile-avatar-status" role="status"></p><div class="profile-avatar-actions"><button class="primary" id="saveAvatar" type="submit">保存头像</button><button id="removeAvatar" type="button">移除头像</button><button id="cancelAvatar" type="button" class="link">取消更改</button></div></form>`}
function updateAvatarControls(){
 const form=$('#avatarForm');if(!form||!state)return;
 const current=avatarDraft===null?state.user.avatar:avatarDraft,dirty=avatarDraft!==null;
 paintAvatar($('#avatarPreview'),current);
 $('#avatarFile').disabled=!!avatarBusy;$('#saveAvatar').disabled=!!avatarBusy||!dirty;$('#removeAvatar').disabled=!!avatarBusy||!safeAvatar(current);$('#cancelAvatar').disabled=!!avatarBusy||!dirty;
 $('#avatarStatus').textContent=avatarBusy==='prepare'?'正在处理图片…':avatarBusy==='save'?'正在保存头像…':dirty?(avatarDraft?'新头像尚未保存。':'将移除头像，点击「保存头像」后生效。'):'头像已与账号同步。';
}
async function prepareAvatar(file){
 if(!['image/png','image/jpeg','image/webp'].includes(file.type))throw Error('请选择 JPG、PNG 或 WebP 图片。');
 if(!file.size||file.size>10*1024*1024)throw Error('图片大小需在 10 MB 以内。');
 const objectURL=URL.createObjectURL(file);
 try{
  const image=await new Promise((resolve,reject)=>{const candidate=new Image();candidate.onload=()=>resolve(candidate);candidate.onerror=()=>reject(Error('无法读取这张图片，请换一张再试。'));candidate.src=objectURL});
  const width=image.naturalWidth,height=image.naturalHeight;if(!width||!height||width>8192||height>8192)throw Error('图片宽高需在 8192 像素以内，请先缩小图片。');
  const canvas=document.createElement('canvas');canvas.width=192;canvas.height=192;const context=canvas.getContext('2d');if(!context)throw Error('当前浏览器无法处理图片，请换一个浏览器再试。');
  context.fillStyle='#ffffff';context.fillRect(0,0,192,192);const edge=Math.min(width,height);context.drawImage(image,(width-edge)/2,(height-edge)/2,edge,edge,0,0,192,192);
  for(const quality of [.85,.7,.55,.4,.25]){const avatar=canvas.toDataURL('image/jpeg',quality);if(safeAvatar(avatar)&&new TextEncoder().encode(JSON.stringify({avatar})).length<=65536)return avatar}
  throw Error('图片处理后仍然过大，请换一张再试。');
 }finally{URL.revokeObjectURL(objectURL)}
}
async function selectAvatar(event){
 const input=event.target,file=input.files&&input.files[0];if(!file||!state||avatarBusy)return;
 const selection=++avatarSelection,owner=state.user.username;avatarBusy='prepare';updateAvatarControls();
 try{const avatar=await prepareAvatar(file);if(selection===avatarSelection&&state&&state.user.username===owner){avatarDraft=avatar;toast('已预览新头像，点击「保存头像」后生效。')}}
 catch(error){if(selection===avatarSelection)toast(error.message||'图片处理失败，请重新选择。')}
 finally{input.value='';if(selection===avatarSelection){avatarBusy='';updateAvatarControls()}}
}
async function saveAvatar(event){
 event.preventDefault();if(!state||avatarBusy||avatarDraft===null)return;
 const avatar=avatarDraft,owner=state.user.username,selection=++avatarSelection;avatarBusy='save';updateAvatarControls();
 try{await api('/avatar','PATCH',{avatar});if(selection===avatarSelection&&state&&state.user.username===owner){state.user.avatar=avatar;avatarDraft=null;updateHeaderAvatar();toast(avatar?'头像已保存，其他设备登录后也会显示。':'头像已移除。')}}
 catch(error){if(selection===avatarSelection)toast(error.message||'头像保存失败，请稍后重试。')}
 finally{if(selection===avatarSelection){avatarBusy='';updateAvatarControls()}}
}
// Display-only self-study guidance; course IDs, descriptions and lesson progress stay in the account.
const AI_STUDY_ROUTE={"title":"汉阳ERICA人工智能专业补课路线","verified_on":"2026-11-05","source_url":"https://m.hanyang.ac.kr/v3/hy_curriculum.page?daehak_cd=Y0001165&hakgwa_cd=Y0001277","basis":["2020-2023","2025-2028"],"course_mappings":[{"title":"尚硅谷Python视频","aliases":[],"school_codes":["CSE1017","CSE2016","DAI1002","CUL7133"],"phase":"now","order":1,"stop":"本轮学到「141_文件操作_小练习」就停；视频第 141 个。重点掌握变量、条件循环、容器、函数、基本类与对象、模块、异常和文件读写。","skip":"多重继承、抽象类、复杂魔法方法、闭包/装饰器、手写迭代器/生成器可先略看用途；142–172 的多进程、多线程、协程暂不学。","checkpoint":"独立完成猜数字和文件记账小程序，能用列表/字典组织数据、函数拆分逻辑、处理错误并保存/读取文件；能定义一个简单类并读懂报错。达标后转数据分析，不停留刷高级语法。","reason":"与学校基础/核心课衔接，复用已有资源避免重复。"},{"title":"数学预备：代数、函数、指数与对数","aliases":[],"school_codes":["GEN2052","DAI2002"],"phase":"now","order":2,"stop":"学完代数运算、一次方程、坐标图像、函数、指数和对数就停；不要求刷完整个数学乐网站。","skip":"奥数、复杂恒等变形、竞赛题和考研题；基础已经会的部分直接跳过。","checkpoint":"独立解简单方程，画一次函数和指数函数，能说明对数是指数的逆运算。达标后把主要时间交给 Python。","reason":"与学校基础/核心课衔接，复用已有资源避免重复。"},{"title":"高等数学与微积分（宋浩完整课程）","aliases":[],"school_codes":["GEN2052","DAI2002"],"phase":"now","order":3,"stop":"学函数/极限的直觉、导数与求导法则、链式法则、极值、积分基本概念、多元函数的偏导与梯度，到能解释并手算一个简单梯度下降步骤就停。","skip":"考研题、复杂证明、技巧性积分、级数、曲线/曲面积分和微分方程等当前不用的内容；无需先刷完整套大学高数。","checkpoint":"能求简单多项式与复合函数导数、二元函数偏导，说明梯度的方向，用 Python 画函数/导数并跑一个最简单的梯度下降。遇到新公式再补对应章节。","reason":"与学校基础/核心课衔接，复用已有资源避免重复。"},{"title":"线性代数（宋浩完整课程）","aliases":[],"school_codes":["MAT2003","DAI2002"],"phase":"now","order":4,"stop":"学向量、矩阵与形状、矩阵乘法、线性方程组与消元、秩与线性相关、基/维数、内积与正交，到特征值和特征向量的基本概念就停。","skip":"考研证明与技巧题、复杂行列式计算、二次型大量题型、抽象空间证明；不同时刷多个线代版本。","checkpoint":"能判断矩阵维度是否匹配，手算小矩阵乘法与消元，解释线性相关、内积和特征向量，再用 NumPy 验证。这个程度先接机器学习即可。","reason":"与学校基础/核心课衔接，复用已有资源避免重复。"},{"title":"概率论与数理统计（宋浩完整课程）","aliases":[],"school_codes":["MAT4073","INE2018","ELE3037"],"phase":"now","order":5,"stop":"学条件概率/贝叶斯、随机变量与常见分布、期望方差、联合分布/协方差，接着学样本与总体、参数估计、置信区间和假设检验的直觉，到能解释一次抽样或模型评估的不确定性就停。","skip":"考研题、复杂分布变换计算、长篇证明和机械套公式刷题；连续分布计算遇到积分困难时只补所需积分。","checkpoint":"会算简单条件概率和期望方差，说明相关不等于因果、样本不等于总体，用 Python 模拟抽样并解释均值波动；不要求考研数学熟练度。","reason":"与学校基础/核心课衔接，复用已有资源避免重复。"},{"title":"尚硅谷C语言精讲","aliases":[],"school_codes":["CSE2018","CSE4009","ENE1004"],"phase":"now","order":6,"stop":"只补编译运行、变量/条件循环、数组、函数、地址与基本指针；能读懂简单数组/指针程序后，按教授课件需要补内存和结构体。","skip":"不连续刷整门129视频、复杂函数指针/回调或应试题。","checkpoint":"能编译小程序，解释数组地址、指针解引用和函数传参，识别一次越界或无效指针。","reason":"计算机架构与系统编程需要 C 和内存基础；先完成这部分基础，再结合教授课件练习。"},{"title":"计算机组成原理-系统学习+考研408","aliases":[],"school_codes":["ENE1004","GEN1030"],"phase":"now","order":7,"stop":"组成原理按知识块学习：标题013–019(表示)、047–058(存储层次)、064–070(Cache)、082–092(指令寻址)、104–117(CPU/流水线/数据通路)；需要时再补其他块。以上为标题编号，不是连续播放或表格序号。C先补编译运行、变量/循环、数组/基本指针/函数；不要求整门129视频。","skip":"跳过考纲/真题/章节刷题；不把“不考研”等同于不学必要架构计算。微程序细节按教授课件需要补。","checkpoint":"解释一条指令的取指/译码/执行，画CPU-内存-Cache关系；算一个简单补码、Cache命中率/流水线例子(以教授要求为准)。","reason":"与学校基础/核心课衔接，复用已有资源避免重复。"},{"title":"自动驾驶技术基础 · 百度Apollo × Udacity（101课）","aliases":["自动驾驶导论 · 百度Apollo官方（17课题）"],"curriculum_key":"ai-autodriving-apollo-intro","school_codes":["DAI3008"],"phase":"now","order":8,"stop":"先学第1章第3、5–9节，理解无人驾驶的定义、运作方式、团队与软硬件架构；接着学第2–7章各模块的简介、作用与课程综述，掌握高精地图、定位、感知、预测、规划、控制的输入输出和关系即可。本阶段不要求连续刷完101节，具体每天学哪些仍由你决定。","skip":"当前跳过欢迎辞、纳米学位宣传；先不钻GNSS/惯导、反向传播、RNN、Lattice、LQR和MPC的公式推导或代码细节，不重复已有机器学习/深度学习主课。不在本阶段安装Apollo、Linux/CUDA或操作车辆，也不购买云实验、证书或硬件。","checkpoint":"能画出地图与定位、感知、预测、规划、控制之间的关系，为每个模块说清输入、输出及一个失败风险，并结合教授本周内容提出一个具体问题。后续具备Python、数学和模型基础后，再补所需算法与仿真实践。","reason":"替换开始学习不可用的旧17课题导论，使用官网面向零基础的现有模块入门课；当前只建立概念地图，不提前要求环境安装或完整系统实践。"},{"title":"尚硅谷Python数据分析","aliases":[],"school_codes":["DAI2001","INE2018","SCS4001"],"phase":"later","order":1,"stop":"学到「069-项目实战-朝向分析」就停，完成课程这一份房价数据分析项目；视频第 69 个，也是当前目录最后一个视频。","skip":"不新增另一套 NumPy/Pandas/Matplotlib 入门；工具安装已经会就跳过，不追求一遍记住所有函数。","checkpoint":"关掉讲解，独立读取一份公开 CSV、处理缺失与异常、筛选分组，并画至少三张有解释的图，保存 Notebook 和结论。做出这一份完整分析即可进入机器学习。","reason":"与学校基础/核心课衔接，复用已有资源避免重复。"},{"title":"数据结构与算法入门（Python）","aliases":[],"school_codes":["CSE2010","ELE3034","DAI2006"],"phase":"later","order":2,"stop":"基础先学复杂度直觉、数组与链表、栈与队列、哈希表、基本二分查找和简单排序就停；能为一个小程序选择合适容器即可。","skip":"算法竞赛、考研证明、大量刷题、复杂树/图、动态规划和高级技巧暂缓；不是先刷完整本算法书才能学 AI。","checkpoint":"能解释列表和字典适合什么场景，写出栈/队列小例子，在有序列表中做二分查找，并大致比较线性与平方时间开销。","reason":"与学校基础/核心课衔接，复用已有资源避免重复。"},{"title":"Git 与版本控制基础","aliases":[],"school_codes":["CSE1019","DAI1001","CSE2024"],"phase":"later","order":3,"stop":"完成版本库、提交与查看差异、撤销、远程仓库、创建与合并分支，能解决一个简单冲突就停。","skip":"复杂 rebase、多人协作策略、服务器搭建、Git 内部原理；不要求刷完全部高级命令。","checkpoint":"在独立练习项目做三次有意义的提交，创建并合并一个分支，推送自己的源码；能找回一次误改，不上传数据库与密码。","reason":"与学校基础/核心课衔接，复用已有资源避免重复。"},{"title":"Linux / Shell 命令行基础","aliases":[],"school_codes":["CSE1019","DAI1001","CSE2018"],"phase":"later","order":4,"stop":"学完当前 Shell 讲义的终端、相对与绝对路径、文件操作、管道、重定向、环境变量和查看帮助，就停在能进入项目并运行 Python 脚本。","skip":"Shell 复杂脚本、系统运维、服务器购买、重装 Linux，以及同系列中与现有 Git 或 AI 编程课重叠的内容。","checkpoint":"能从终端进入指定目录、列出与读取文件、运行一个 Python 文件，并把输出保存为文本；Windows 可先在现有 PowerShell 练习对应操作。","reason":"与学校基础/核心课衔接，复用已有资源避免重复。"},{"title":"SQL 与关系数据库基础（廖雪峰完整教程）","aliases":[],"school_codes":["CSE3010"],"phase":"later","order":5,"stop":"先学关系模型、主键外键、SELECT/WHERE、排序、聚合/GROUP BY、多表 JOIN，再了解基本增删改和事务，能从两张练习表正确取数就停。","skip":"MySQL 服务器运维、复杂事务隔离机制、性能调优和分布式数据库；不用先装复杂服务器，在线 SQL/本地 SQLite 练习即可。","checkpoint":"独立完成筛选、分组统计和两表连接；在本地练习库用 Python 参数绑定读取数据，知道不能把真实学习记录当练习数据。","reason":"与学校基础/核心课衔接，复用已有资源避免重复。"},{"title":"尚硅谷AI大模型之机器学习","aliases":[],"school_codes":["MMT3007","INE5006"],"phase":"later","order":6,"stop":"先到111_集成学习_随机森林并完成评估专项；随后补112–120聚类、SVD/PCA，与数据挖掘课程衔接，不再永久跳过。","skip":"牛顿/拟牛顿与SVM复杂推导先按课件需求，不为刷课购买GPU。","checkpoint":"独立做一个回归和分类项目；有基线、正确指标、划分及仅训练集fit的Pipeline，能解释过拟合。","reason":"与学校基础/核心课衔接，复用已有资源避免重复。"},{"title":"模型评估与数据泄漏防范（scikit-learn 实践专项）","aliases":[],"school_codes":["MMT3007","INE5006"],"phase":"later","order":7,"stop":"读完 Common pitfalls 的预处理不一致、数据泄漏和随机性控制三个主题，完成一个 Pipeline 的错误/正确对照实验就停。官方正文为英文，可用浏览器翻译。","skip":"整个 scikit-learn API 手册、所有算法与所有调参方法；不再刷第二套机器学习入门。","checkpoint":"能先划分数据、仅用训练集 fit 预处理，以 Pipeline 做验证；解释错误方法为什么虚高，用固定划分复现一次结果并写明最终测试集只作验收。","reason":"与学校基础/核心课衔接，复用已有资源避免重复。"},{"title":"尚硅谷AI大模型之深度学习","aliases":[],"school_codes":["CSE4048","DAI2001","ITE4052","DAI3008"],"phase":"later","order":8,"stop":"本轮学到「126_服装分类案例_模型测试」就停。包含 PyTorch、训练循环、保存与加载模型、简单 CNN 和完整服装分类案例；视频第 126 个。","skip":"127–138 的 NLP/词嵌入/RNN 留到选择文本方向后；复杂优化器比较、公式证明和大规模 GPU 训练不作为阶段完成条件。","checkpoint":"能独立写 Dataset/DataLoader 和训练/验证循环，用小型 CNN 做图像分类，保存后重新加载并预测；能检查错误样本，说明训练结果不等于测试结果。","reason":"与学校基础/核心课衔接，复用已有资源避免重复。"},{"title":"计算机视觉补充 · 李沐《动手学深度学习》精选","aliases":[],"curriculum_key":"ai-cv-d2l-selected","school_codes":["ITE4052","DAI3004","DAI3008"],"phase":"later","order":9,"stop":"先完成已有PyTorch/CNN主课，再学习本目录13个视频：图像增广→微调→检测/边界框/数据集/锚框→R-CNN等检测思路→多尺度与SSD实现→语义分割/数据集→转置卷积→FCN；做一个迁移学习或检测/分割小实验后停止，不从头重学整套深度学习。","skip":"不重复Python、线代、自动求导、MLP、CNN入门或NLP；跳过多GPU/分布式训练、竞赛和样式迁移；不要求从零实现所有检测器或追赶最新模型。","checkpoint":"能区分分类、检测与分割；说明数据增强/微调何时有用；在小数据或现成模型上完成一次推理/微调，展示框或掩码，并分析错误样例。","reason":"已有深度学习主课止于CNN分类；只补图像增强/微调/检测/分割13个精选视频，不重复整套DL。"},{"title":"尚硅谷AI大模型之NLP教程","aliases":[],"school_codes":["DAI4002"],"phase":"later","order":10,"stop":"先掌握文本表示与词向量，再学习 Attention、Transformer 和 Hugging Face 模型/Tokenizer 调用，学到「168-NLP-预训练模型-HF-Tokenizer-模型配合使用」就停。这里是视频列表第 168 个，不是混有资料的表格行号。","skip":"当前 Python、数学阶段整门暂缓；之后也不要求一次刷完全部 RNN/LSTM/Seq2Seq 案例或 169 起的数据集、训练与微调实战；大模型从头训练暂不做。","checkpoint":"能说明输入如何变成 token、Attention 大致做什么，用一个现成的小模型完成一次文本推理，解释输入输出和失败样例；不要求购买 GPU。","reason":"先完成 Python、数学与模型基础，再学习文本表示、Attention 与 Transformer。"},{"title":"AI 项目交付与接口部署（FastAPI 中文教程）","aliases":[],"school_codes":["CSE2024","CSE4060","CSE4061"],"phase":"later","order":11,"stop":"学到请求体、响应模型、输入验证、错误处理与基础测试，做出一个本地模型推理接口就先停；再读部署概念和 HTTPS 了解正式运行的区别。","skip":"复杂认证、微服务、Kubernetes、大规模并发与实付云部署；不要求开通 FastAPI Cloud 或购买 GPU。","checkpoint":"已有轻量模型能通过本地接口预测，非法输入能被拒绝，重启后可运行；提供健康检查、依赖和启动文档，别人按说明能复现。","reason":"完成一个可运行的基础模型后，用小项目学习接口与部署。"}],"highlights":["两版共同强调编程、数学、张量、ML、DL，补课先编程/数学再模型。","机器学习从大三第2学期前移至大二第2学期。","计算机架构从大三第1学期前移至大二第2学期，当前并行补。","AI数学从大二第2学期前移至大二第1学期；新版未单列线代不等于不需要。","新版概率与统计安排在大一第2学期，不拖到DL以后补。","DL仍在大三第1学期，新版由专业深化改为专业核心。","CV、NLP、AI与自动驾驶两版均在大三第2学期；自学DL后优先衔接当前自动驾驶所需视觉。","新版新增强化学习、物理AI、GPU编程、AI安全等后续方向，掌握当前基础后再考虑对应课程。"],"school_courses":{"CSE1017":{"code":"CSE1017","title_ko":"프로그래밍기초","title_zh":"编程基础"},"CSE1019":{"code":"CSE1019","title_ko":"오픈소스Sw기초","title_zh":"开源软件基础"},"CSE2010":{"code":"CSE2010","title_ko":"자료구조론","title_zh":"数据结构"},"CSE2016":{"code":"CSE2016","title_ko":"프로그램설계방법론","title_zh":"程序设计方法论"},"CSE2018":{"code":"CSE2018","title_ko":"시스템프로그래밍기초","title_zh":"系统编程基础"},"CSE2022":{"code":"CSE2022","title_ko":"피지컬컴퓨팅","title_zh":"物理计算"},"CSE2024":{"code":"CSE2024","title_ko":"소프트웨어개발실무","title_zh":"软件开发实践"},"CSE3010":{"code":"CSE3010","title_ko":"데이터베이스","title_zh":"数据库"},"CSE4009":{"code":"CSE4009","title_ko":"시스템프로그래밍","title_zh":"系统编程"},"CSE4048":{"code":"CSE4048","title_ko":"딥러닝","title_zh":"深度学习"},"CSE4060":{"code":"CSE4060","title_ko":"소프트웨어융합캡스톤1","title_zh":"软件融合Capstone1"},"CSE4061":{"code":"CSE4061","title_ko":"소프트웨어융합캡스톤2","title_zh":"软件融合Capstone2"},"CUL7124":{"code":"CUL7124","title_ko":"Ai리터러시","title_zh":"AI素养"},"CUL7133":{"code":"CUL7133","title_ko":"파이썬과인공지능","title_zh":"Python与人工智能"},"DAI1001":{"code":"DAI1001","title_ko":"오픈소스실전인공지능","title_zh":"开源实践人工智能"},"DAI1002":{"code":"DAI1002","title_ko":"인공지능코딩","title_zh":"人工智能编程"},"DAI1003":{"code":"DAI1003","title_ko":"바이브코딩","title_zh":"Vibe Coding"},"DAI2001":{"code":"DAI2001","title_ko":"텐서프로그래밍","title_zh":"张量编程"},"DAI2002":{"code":"DAI2002","title_ko":"인공지능수학","title_zh":"人工智能数学"},"DAI2006":{"code":"DAI2006","title_ko":"자료구조와알고리즘","title_zh":"数据结构与算法"},"DAI3004":{"code":"DAI3004","title_ko":"시각지능학습","title_zh":"视觉智能学习"},"DAI3005":{"code":"DAI3005","title_ko":"음성인식","title_zh":"语音识别"},"DAI3007":{"code":"DAI3007","title_ko":"강화학습","title_zh":"强化学习"},"DAI3008":{"code":"DAI3008","title_ko":"인공지능과자율주행","title_zh":"人工智能与自动驾驶"},"DAI3009":{"code":"DAI3009","title_ko":"피지컬인공지능","title_zh":"物理人工智能"},"DAI3010":{"code":"DAI3010","title_ko":"피지컬Ai실습(입문)","title_zh":"物理AI实践（入门）"},"DAI4002":{"code":"DAI4002","title_ko":"자연어처리","title_zh":"自然语言处理"},"DAI4005":{"code":"DAI4005","title_ko":"고급딥러닝","title_zh":"高级深度学习"},"DAI4007":{"code":"DAI4007","title_ko":"Gpu프로그래밍","title_zh":"GPU编程"},"DAI4009":{"code":"DAI4009","title_ko":"Ai보안","title_zh":"AI安全"},"ELE3034":{"code":"ELE3034","title_ko":"알고리즘설계와분석","title_zh":"算法设计与分析"},"ELE3037":{"code":"ELE3037","title_ko":"확률과통계","title_zh":"概率与统计"},"ENE1004":{"code":"ENE1004","title_ko":"컴퓨터구조","title_zh":"计算机架构"},"GEN0063":{"code":"GEN0063","title_ko":"일반물리학1","title_zh":"普通物理1"},"GEN1030":{"code":"GEN1030","title_ko":"컴퓨터개론","title_zh":"计算机概论"},"GEN2052":{"code":"GEN2052","title_ko":"미분적분학1","title_zh":"微积分1"},"INE2018":{"code":"INE2018","title_ko":"전산통계학","title_zh":"计算统计学"},"INE5006":{"code":"INE5006","title_ko":"데이터마이닝","title_zh":"数据挖掘"},"ITE4052":{"code":"ITE4052","title_ko":"컴퓨터비전","title_zh":"计算机视觉"},"MAT2003":{"code":"MAT2003","title_ko":"선형대수","title_zh":"线性代数"},"MAT4073":{"code":"MAT4073","title_ko":"확률론","title_zh":"概率论"},"MMT2009":{"code":"MMT2009","title_ko":"인공지능의이해","title_zh":"人工智能概论"},"MMT3007":{"code":"MMT3007","title_ko":"기계학습","title_zh":"机器学习"},"SCS4001":{"code":"SCS4001","title_ko":"데이터사이언스","title_zh":"数据科学"},"ITE3062":{"code":"ITE3062","title_ko":"인간컴퓨터상호작용","title_zh":"人机交互"},"CSE3036":{"code":"CSE3036","title_ko":"빅데이터검색","title_zh":"大数据检索"}},"comparison":{"old_all_courses":64,"new_all_courses":62,"same_code_courses":44,"changes":[{"code":"ENE1004","old_year":"3","old_semester":"1학기","old_classification":"전공핵심","old_credits":"3","new_year":"2","new_semester":"2학기","new_classification":"전공핵심","new_credits":"3"},{"code":"CSE4048","old_year":"3","old_semester":"1학기","old_classification":"전공심화","old_credits":"3","new_year":"3","new_semester":"1학기","new_classification":"전공핵심","new_credits":"4"},{"code":"MMT3007","old_year":"3","old_semester":"2학기","old_classification":"전공핵심","old_credits":"3","new_year":"2","new_semester":"2학기","new_classification":"전공핵심","new_credits":"3"},{"code":"ITE3062","old_year":"4","old_semester":"2학기","old_classification":"전공심화","old_credits":"3","new_year":"3","new_semester":"2학기","new_classification":"전공심화","new_credits":"3"},{"code":"CSE3036","old_year":"3","old_semester":"1학기","old_classification":"전공심화","old_credits":"3","new_year":"4","new_semester":"1학기","new_classification":"전공심화","new_credits":"3"},{"code":"DAI2002","old_year":"2","old_semester":"2학기","old_classification":"전공핵심","old_credits":"3","new_year":"2","new_semester":"1학기","new_classification":"전공핵심","new_credits":"3"}]}};
function isCloud(){return !!state&&state.runtime==='cloud'}
function localReadable(c){return !!c&&!isCloud()&&c.available!==false}
function courseStorage(c){if(!c.local_key)return {label:'在线资料',style:'online'};if(isCloud())return {label:'本地视频未上传',style:'offline'};return c.available===false?{label:'硬盘未连接',style:'offline'}:{label:'硬盘已连接',style:'connected'}}
function storageLabel(c){const s=courseStorage(c);return `<small class="source-location storage-state ${s.style}">${s.label}</small>`}
function courseURL(c){try{const value=String(c.url||'');const url=new URL(value);return ['http:','https:'].includes(url.protocol)&&!url.username&&!url.password&&!/\s/.test(value)?value:''}catch{return ''}}
function onlineCourse(c,label='在线学习 ↗'){if(c.curriculum_key==='english-zh-ielts-dut')label='官网查课 ↗';const url=courseURL(c);return url?`<a class="button primary" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`:''}
function aiCourseRoute(c){
 if(!c||courseCategory(c)!=='ai')return null;
 const title=String(c.title||'').trim(),key=String(c.curriculum_key||'');
 return AI_STUDY_ROUTE.course_mappings.find(row=>(key&&row.curriculum_key===key)||row.title===title||(row.aliases||[]).includes(title))||null;
}
function aiCoursePhase(c){return aiCourseRoute(c)?.phase==='now'?'now':'later'}
function aiCoursePhaseLabel(c){return aiCoursePhase(c)==='now'?'现在需要学':'以后需要学'}
function compareCourseRows(a,b,category){
 if(category==='ai'){
  const phase=(aiCoursePhase(a.course)==='now'?0:1)-(aiCoursePhase(b.course)==='now'?0:1);
  if(phase)return phase;
  const left=aiCourseRoute(a.course),right=aiCourseRoute(b.course);
  if(!!left!==!!right)return left?-1:1;
  return (left&&right?left.order-right.order:0)||a.index-b.index;
 }
 return (category==='english'?Number((courseAdvice(a.course).priority||'P9').slice(1))-Number((courseAdvice(b.course).priority||'P9').slice(1)):0)||courseAdvice(a.course).stage-courseAdvice(b.course).stage||a.index-b.index;
}
function courseSummary(c){const route=aiCourseRoute(c);return route?`${aiCoursePhaseLabel(c)}\n${route.reason}\n当前学到：${route.stop}`:c.description||'按需查阅，不自动生成每日任务。'}
function aiSchoolCourseHTML(codes){return `<ul class="ai-school-courses">${codes.map(code=>{const school=AI_STUDY_ROUTE.school_courses[code];return school?`<li><strong>${esc(school.title_zh||school.title_ko)}</strong><small>${esc(school.title_ko)} · ${esc(code)}</small></li>`:''}).join('')}</ul>`}
function aiCourseRouteDescriptionHTML(c){
 const route=aiCourseRoute(c);
 if(!route)return `<div class="course-guide-description">${esc(c.description||'这门课程由你自主安排。可以先查看课程内容，再决定是否加入每日任务。')}</div>`;
 const description=String(c.description||''),systemDescription=/^\s*【(?:主线必学|基础配套|进阶选学|备查资料|专业选学)[｜|·]\s*阶段\d{1,2}】/.test(description);
 const original=description&&!systemDescription?`<details class="course-original-description"><summary>原课程说明</summary><div class="course-guide-description">${esc(description)}</div></details>`:'';
 return `<section class="ai-course-context"><p><strong>${aiCoursePhaseLabel(c)}</strong></p><p>${esc(route.reason)}</p>${route.school_codes.length?`<details><summary>对应汉阳校课 · ${route.school_codes.length} 门</summary>${aiSchoolCourseHTML(route.school_codes)}</details>`:''}<p class="muted">学习范围用于衔接专业知识；学校课程要求以本人适用培养方案和教授课件为准。</p></section>${original}`;
}
function courseAdvice(c){
 if(courseCategory(c)==='ai')return {label:aiCoursePhaseLabel(c),stage:999,priority:'',style:'phase-'+aiCoursePhase(c)};
 const description=String(c.description||''),match=description.match(/^\s*【(主线必学|基础配套|进阶选学|备查资料|专业选学)[｜|·]\s*阶段(\d{1,2})】/),explicit=description.match(/优先级[：:]\s*(P[0-3])\b/);
 if(courseCategory(c)==='english'){
  const roles={main:{label:c.instruction_language==='中文讲解'?'中文主课':'主课',style:'required',priority:'P0'},practice:{label:'配套练习',style:'foundation',priority:'P2'},reference:{label:'选学参考',style:'optional',priority:'P3'},ielts:{label:'雅思专项',style:'foundation',priority:'P1'}},role=roles[c.learning_role];
  return {label:role?role.label:'自主安排',stage:999,style:role?role.style:'custom',priority:explicit?explicit[1]:role?role.priority:''};
 }
 if(!match)return {label:'自主安排',stage:Number.isInteger(c.stage)?c.stage:999,style:'custom',priority:explicit?explicit[1]:''};
 const label=match[1],stage=+match[2],core=label==='主线必学'||label==='基础配套';
 return {label,stage,priority:explicit?explicit[1]:core?(stage<=1?'P0':'P1'):label==='进阶选学'?'P2':'P3',style:label==='主线必学'?'required':label==='基础配套'?'foundation':'optional'};
}
function courseAdviceHTML(c){const advice=courseAdvice(c);return `<small class="course-advice ${advice.style}">${advice.priority?`<strong class="course-priority">${advice.priority}</strong>`:''}${esc(advice.label)}${advice.stage===999?'':` · 阶段 ${advice.stage}`}</small>`}
function courseLanguageHTML(c){const language=String(c.instruction_language||'').trim();return language||courseCategory(c)==='english'?`<small class="course-language${language==='中文讲解'?' chinese-instruction':''}">授课语言：${esc(language||'未标注，请查看原课程')}</small>`:''}
function courseUnitLabel(c){return String(c&&c.unit_label||'').trim()||'节'}
function courseScope(c){const route=aiCourseRoute(c);if(route)return {'当前学到':route.stop,'暂时跳过':route.skip,'达标自测':route.checkpoint};const result={};String(c.description||'').split(/\r?\n/).forEach(line=>{const match=line.match(/^\s*(当前学到|暂时跳过|达标自测)[：:]\s*(.*)$/);if(match&&!result[match[1]]&&match[2].trim())result[match[1]]=match[2].trim()});return result}
function courseStopHTML(c){const stop=courseScope(c)['当前学到'];return stop?`<small class="course-stop" title="${esc('当前学到：'+stop)}">当前学到：${esc(stop)}</small>`:''}
function courseScopeHTML(c){const scope=courseScope(c),rows=['当前学到','暂时跳过','达标自测'].filter(label=>scope[label]);return rows.length?`<section class="course-scope-panel" aria-label="当前学习边界"><dl>${rows.map(label=>`<div><dt>${label}</dt><dd>${esc(scope[label])}</dd></div>`).join('')}</dl></section>`:''}
function openCourseGuide(c){
 if(!c)return;
 let dialog=$('#courseGuideDialog');
 if(!dialog){dialog=document.createElement('dialog');dialog.id='courseGuideDialog';dialog.className='course-guide-dialog';dialog.setAttribute('aria-labelledby','courseGuideTitle');document.body.appendChild(dialog)}
 dialog.innerHTML=`<div class="course-guide-heading"><span class="eyebrow">学习说明</span><h2 id="courseGuideTitle">${esc(c.title)}</h2>${courseAdviceHTML(c)}${courseLanguageHTML(c)}</div>${courseScopeHTML(c)}${aiCourseRouteDescriptionHTML(c)}<p class="muted course-guide-hint">这里只提供学习范围和建议顺序。每天学哪一项、学多久，由你自己决定。</p><div class="actions course-guide-actions">${onlineCourse(c,'打开完整课程 ↗')}<button type="button" id="closeCourseGuide">关闭</button></div>`;
 $('#closeCourseGuide').onclick=()=>dialog.close();dialog.showModal();
}
function materialsNote(c){if(!c.local_key){if(c.curriculum_key==='english-zh-ielts-sun-32')return '这套中文视频课共8个单元，每单元阅读、写作、听力、口语各一节。点击「打开课程」观看对应视频；抽查时未登录可用「360P 流畅」播放，高清、收藏和领取配套讲义可能需要原平台账号。先试第一单元，熟悉的内容可自测后跳过；学完相应视频并完成练习后回到这里打钩。';if(c.curriculum_key==='english-zh-ielts-dut')return '这是需要中国大学MOOC账号的选学参考；当前免注册中文主课是Sun老师的32节视频。若需要本课，点「官网查课」，在结果中选择大连理工大学的进行中班级。登录后的实际视频和免费权限尚未核验，33行仅为公开目录课题。';return '通过外部课程页面学习；完成后在这里手动打钩，进度保存在自己的账号。这里只保存链接和学习记录，视频、音频与教材由原网站提供。'}if(isCloud())return c.url?'可通过「在线学习」打开课程播放页并选择课时。下表保留你的硬盘目录，在线版本的课时名称或顺序可能不同；原始资料仍在移动硬盘。':'云端保留课程与课时目录，视频和原始资料仍在移动硬盘。填写课程的在线链接后，即可从电脑和平板打开学习。';if(!localReadable(c))return c.url?'移动硬盘未连接，可先点击「在线学习」。连接硬盘后刷新状态，即可读取原始资料。':'移动硬盘未连接或课程目录暂不可用，课程没有删除。重新连接硬盘后点击「刷新硬盘状态」，即可重新检查。';return '正在读取移动硬盘中的原始资料，不会复制整套视频到电脑。'}
function lessonSizeHTML(l){const duration=Number(l.duration_seconds);if((l.kind==='external'||l.url)&&Number.isFinite(duration)&&duration>0)return esc(formatTaskDuration(duration*1000))+'<small class="course-progress">视频时长</small>';return l.kind==='external'||l.url?'外部课程页':Number.isFinite(l.size)?(l.size/1048576).toFixed(1)+' MB':'—'}
function materialAction(c,l){if(l.kind==='external'||l.url){const url=courseURL({url:l.url});return url?`<a class="button primary" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${c.curriculum_key==='english-zh-ielts-dut'?'官网查课 ↗':'打开课程 ↗'}</a>`:'<button type="button" disabled>暂未提供链接</button>'}if(!localReadable(c)){if(l.kind==='video'&&c.url)return onlineCourse(c,'在线选集 ↗');return `<button type="button" disabled title="${isCloud()?'原始视频和资料未上传到云端，请在本机连接移动硬盘后打开':'请连接移动硬盘后刷新状态'}">${isCloud()?'资料在本地硬盘':'需连接硬盘'}</button>`}return l.kind==='video'?`<button data-play="${l.id}">播放</button>`:`<a class="button" href="${API}/materials/${encodeURIComponent(l.id)}" target="_blank" rel="noopener">打开资料</a>`}
function taskPaused(t){return !!t&&!t.done&&!!t.started_at&&!t.finished_at&&Array.isArray(t.pause_intervals)&&t.pause_intervals.some(interval=>interval&&interval.finished_at===null&&typeof interval.started_at==='string'&&Number.isFinite(Date.parse(interval.started_at)))}
function taskStatus(t){return t.done?{label:'✓ 已完成',style:'finished'}:taskPaused(t)?{label:'已暂停',style:'paused'}:t.started_at&&!t.finished_at?{label:'进行中',style:'running'}:{label:'待完成',style:''}}
function localDateTime(value){if(!value)return '';const d=new Date(value);if(Number.isNaN(d.getTime()))return '';return ds(d)+'T'+[d.getHours(),d.getMinutes(),d.getSeconds()].map(n=>String(n).padStart(2,'0')).join(':')}
function taskTimeHTML(value){const local=localDateTime(value);return local?`<time datetime="${esc(value)}" title="按当前设备时区显示"><span>${local.slice(0,10)}</span><strong>${local.slice(11)}</strong></time>`:'<span class="muted">—</span>'}
function settledTaskMilliseconds(t){
 const value=t||{},minutes=['number','string'].includes(typeof value.actual)?Number(value.actual):0,legacy=minutes*60000,fallback=Number.isFinite(legacy)&&legacy>=0?legacy:0;
 const hasStart=typeof value.started_at==='string'&&!!value.started_at.trim(),hasFinish=typeof value.finished_at==='string'&&!!value.finished_at.trim();
 const start=hasStart?Date.parse(value.started_at):NaN,finish=hasFinish?Date.parse(value.finished_at):NaN;
 if(Number.isFinite(start)&&Number.isFinite(finish))return finish>=start?Math.max(0,finish-start-taskPauseMilliseconds(start,finish,value.pause_intervals)):0;
 if(hasStart&&!hasFinish&&!value.done)return 0;
 return fallback;
}
function taskPauseMilliseconds(startMs,endMs,intervals){return typeof StudyTaskTimer!=='undefined'&&typeof StudyTaskTimer.pauseMilliseconds==='function'?StudyTaskTimer.pauseMilliseconds(startMs,endMs,intervals):0}
function formatTaskDuration(milliseconds){const seconds=Math.floor(Number.isFinite(milliseconds)?Math.max(0,milliseconds)/1000:0);return [Math.floor(seconds/3600),Math.floor(seconds%3600/60),seconds%60].map(value=>String(value).padStart(2,'0')).join(':')}
function formatTaskMinutes(minutes){return (Number.isFinite(minutes)?Math.max(0,minutes):0).toFixed(1).replace(/\.0$/,'')}
function actualHTML(t){if(window.StudyTaskTimer&&t.started_at&&(!t.done||t.finished_at))return window.StudyTaskTimer.markup(t.started_at,t.finished_at,t.pause_intervals);return t.started_at&&!t.finished_at&&!t.done?`<span class="timing-active">${taskPaused(t)?'已暂停':'计时中'}</span>`:`${t.actual} 分钟`}
function taskTimerButtonHTML(t){if(t.done||t.finished_at)return '';const action=!t.started_at?'start':taskPaused(t)?'resume':'pause',label={start:'开始计时',pause:'暂停计时',resume:'继续计时'}[action],aria={start:'开始',pause:'暂停',resume:'继续'}[action];return `<button type="button" class="${action==='pause'?'':'primary'}" data-task-action="${action}" data-task-id="${t.id}" ${action==='start'?`data-start="${t.id}"`:''} aria-label="${aria} ${esc(t.title)}">${label}</button>`}
function taskControlsBusy(id,busy){const taskId=Number(id);if(!Number.isInteger(taskId))return;const row=$(`.task-table tr[data-task-id="${taskId}"]`);if(!row)return;row.setAttribute('aria-busy',busy?'true':'false');row.querySelectorAll('button,input[type="checkbox"]').forEach(control=>control.disabled=busy)}
function taskHasTime(t){return !!(t.started_at||t.finished_at||Number(t.actual)>0||(Array.isArray(t.pause_intervals)&&t.pause_intervals.length))}
function taskResetButtonHTML(t){return taskHasTime(t)?`<button type="button" class="link task-reset" data-task-reset="${t.id}" aria-label="计时清零 ${esc(t.title)}">计时清零</button>`:''}
function isRestDay(day=selected){return Array.isArray(state?.rest_days)&&state.rest_days.includes(day)}
function restDayButtonHTML(day=selected){const rest=isRestDay(day);return `<button type="button" id="restDayToggle" class="rest-day-toggle${rest?' is-rest-day':''}" aria-pressed="${rest}" aria-label="${rest?'取消':'设置'} ${esc(day)} 的休息日" ${dayRestBusy?'disabled':''}>${rest?'取消休息':'设为休息日'}</button>`}
function restDayBannerHTML(day=selected){return isRestDay(day)?`<section class="rest-day-banner rest-day-center" role="status" aria-label="休息日"><strong>休息</strong><div class="rest-day-center-note"><p>这一天给自己放个假。任务、笔记和已有用时都保留，你也可以自行调整安排。</p><small>取消休息后，需要你手动继续计时。</small></div></section>`:''}
function dailyTaskContentHTML(shown){const table=`<div class="table-scroll" tabindex="0" role="region" aria-label="每日任务表，可左右滚动"><table class="planner-table task-table"><caption>每日任务 · ${esc(selected)} · 共 ${shown.length} 项</caption><colgroup><col class="col-task-complete"><col class="col-task-title"><col class="col-task-day"><col class="col-task-category"><col class="col-task-planned"><col class="col-task-time"><col class="col-task-time"><col class="col-task-actual"><col class="col-task-status"><col class="col-task-actions"></colgroup><thead><tr><th scope="col">完成</th><th scope="col">任务名称</th><th scope="col">日期</th><th scope="col">分类</th><th scope="col">预计用时</th><th scope="col">开始时间</th><th scope="col">结束时间</th><th scope="col">实际用时</th><th scope="col">状态</th><th scope="col">操作</th></tr></thead><tbody>${shown.map(taskHTML).join('')||'<tr><td colspan="10" class="table-empty">暂无任务。点击「新建任务」或在下方输入，添加你自己的安排。</td></tr>'}</tbody></table></div>`;return isRestDay()?restDayBannerHTML()+`<details id="restDayTasks" class="rest-day-task-details" ${restTaskExpanded.has(selected)?'open':''} data-day="${esc(selected)}"><summary>查看当天任务（${shown.length} 项）</summary>${table}</details>`:table}
function taskMutationAllowed(allowedDialog=null){
 if(dayRestBusy){toast('正在更新休息日，请稍候。');return false}
 const openDialog=document.querySelector('dialog[open]');
 if(openDialog&&openDialog!==allowedDialog){toast('请先保存或关闭当前编辑窗口。');return false}
 if(languageHasDirty()||languageWrites.size||languageBatch||languageSettingBusy){toast('请先保存练习，再修改任务。');return false}
 return true;
}
function syncDailyControls(){
 document.querySelectorAll('.task-table tr[data-task-id],.journal-table tr[data-task-id]').forEach(row=>{const busy=dayRestBusy||taskActionWrites.has(String(Number(row.dataset.taskId)));row.setAttribute('aria-busy',busy?'true':'false');row.querySelectorAll('button,input[type="checkbox"]').forEach(control=>control.disabled=busy)});
 document.querySelectorAll('#newTask,#quickAdd input,#quickAdd button,.daily-task-card .date-row button,.daily-task-card .date-row input,.daily-task-card [data-filter]').forEach(control=>control.disabled=dayRestBusy);
 const dateInput=$('#date');if(dateInput)dateInput.min=today();
 const previous=$('#prevDay');if(previous)previous.disabled=dayRestBusy||selected<=today();
 const rest=$('#restDayToggle');if(rest)rest.disabled=dayRestBusy||!!pendingMutations||!!taskActionWrites.size;
}
async function toggleRestDay(){
 if(!state||!taskMutationAllowed())return false;
 if(pendingMutations||taskActionWrites.size){toast('任务正在保存，请稍候再设置休息日。');return false}
 const day=normaliseMainDay(),rest=!isRestDay(day),running=(state.tasks||[]).filter(t=>t.day===day&&!t.done&&t.started_at&&!t.finished_at&&!taskPaused(t));
 if(rest&&running.length&&!window.confirm(`${day} 还有 ${running.length} 项任务正在计时。设为休息日会暂停这些任务，已有用时保留。确定休息吗？`))return false;
 dayRestBusy=true;syncDailyControls();
 try{const result=await api('/days/rest','POST',{day,rest});await refresh();toast(rest?`${day} 已设为休息日${result.paused_tasks?'，已暂停 '+result.paused_tasks+' 项任务':''}`:`${day} 已取消休息，计时由你手动继续`);return true}catch(e){toast(e.message);return false}finally{dayRestBusy=false;syncDailyControls()}
}
async function resetTaskTimer(id){
 if(!state||!taskMutationAllowed())return false;
 id=String(Number(id));const task=state.tasks.find(t=>String(t.id)===id);
 if(!task||!taskHasTime(task)||taskActionWrites.has(id))return false;
 if(!window.confirm(`将「${task.title}」的开始时间、结束时间、暂停记录和实际用时全部清零？\n任务内容、笔记和完成状态都会保留。`))return false;
 taskActionWrites.add(id);syncDailyControls();
 try{await api('/tasks/'+id,'PATCH',{action:'reset'});await refresh();toast('该任务计时已清零，内容、笔记和完成状态保留');return true}catch(e){toast(e.message);return false}finally{taskActionWrites.delete(id);syncDailyControls()}
}
function taskMoveButtonHTML(task){
 if(task.done||task.day==='9999-12-31')return '';
 return `<button type="button" class="link task-move" data-task-move="${task.id}" aria-label="移到日期 ${esc(task.title)}">移到日期</button>`;
}
function taskMoveNoticeHTML(){
 if(!taskMoveNotice||taskMoveNotice.owner!==state.user.username)return '';
 const {day,paused}=taskMoveNotice;
 return `<section class="task-move-notice" role="status"><span>已移到 ${esc(day)}${paused?'，计时已暂停':''}。</span><button type="button" id="viewMovedTaskDay" class="link">查看目标日</button></section>`;
}
function viewMovedTaskDay(){
 if(!taskMoveNotice||taskMoveNotice.owner!==state?.user.username||!taskMutationAllowed())return;
 if(quickAddDirty()){
  toast('请先保存或清空快速添加框里的内容，再查看目标日。');
  return;
 }
 const destination=taskMoveNotice.day;
 taskMoveNotice=null;
 if(destination<today()){
  selected=normaliseMainDay();
  view='journal';
  render();
  toast('该日期已成为历史，请在任务记录中查看。');
  return;
 }
 selected=destination;
 view='today';
 filter='all';
 if(isRestDay())restTaskExpanded.add(selected);
 render();
}
function openTaskMove(id){
 if(!state||!taskMutationAllowed()||taskActionWrites.has(id))return;
 const task=state.tasks.find(item=>String(item.id)===id);
 if(!task||task.done||task.day==='9999-12-31')return;

 // Task dates are calendar days. Use UTC to increment them without local clock shifts.
 const nextDay=new Date(task.day+'T00:00:00Z');
 nextDay.setUTCDate(nextDay.getUTCDate()+1);
 const earliest=nextDay.toISOString().slice(0,10);
 const owner=state.user.username;
 let dialog=$('#taskMoveDialog');
 if(!dialog){
  dialog=document.createElement('dialog');
  dialog.id='taskMoveDialog';
  dialog.setAttribute('aria-labelledby','taskMoveDialogTitle');
  document.body.appendChild(dialog);
 }
 dialog.innerHTML=`<form id="taskMoveForm">
  <h2 id="taskMoveDialogTitle">移到日期</h2>
  <p class="task-move-title">${esc(task.title)}</p>
  <p class="muted">原定 ${esc(task.day)}。请选择之后的任意日期。</p>
  <label>目标日期<input name="day" type="date" required min="${earliest}" max="9999-12-31" value="${earliest}"></label>
  <p class="task-move-error" role="alert"></p>
  <div class="actions"><button type="button" data-move-cancel>取消</button><button type="submit" class="primary">确认移动</button></div>
 </form>`;
 const form=dialog.querySelector('form');
 const errorMessage=dialog.querySelector('[role="alert"]');
 const controls=form.querySelectorAll('input,button');
 form.querySelector('[data-move-cancel]').onclick=()=>dialog.close();
 dialog.oncancel=event=>{if(taskActionWrites.has(id))event.preventDefault()};
 form.onsubmit=async event=>{
  event.preventDefault();
  if(!taskMutationAllowed(dialog)||taskActionWrites.has(id)||state?.user.username!==owner)return;
  if(!form.reportValidity())return;
  const day=form.elements.day.value;
  if(!/^\d{4}-\d{2}-\d{2}$/.test(day)||day<earliest){
   errorMessage.textContent='请选择晚于原定日期的有效日期。';
   return;
  }
  errorMessage.textContent='';
  taskActionWrites.add(id);
  syncDailyControls();
  controls.forEach(control=>control.disabled=true);
  try{
   const result=await api('/tasks/'+id,'PATCH',{action:'reschedule',day});
   dialog.close();
   if(state?.user.username!==owner)return;
   const movedDay=result.day;
   taskMoveNotice={owner,day:movedDay,paused:result.paused_on_move};
   const message=`已移到 ${movedDay}${result.paused_on_move?'，计时已暂停':''}。`;
   try{
    await refresh();
    $('#viewMovedTaskDay')?.focus();
    toast(message);
   }catch{toast(message+'列表暂未刷新，请稍后重新打开。')}
  }catch(error){
   errorMessage.textContent=error.message;
  }finally{
   taskActionWrites.delete(id);
   controls.forEach(control=>control.disabled=false);
   syncDailyControls();
  }
 };
 dialog.showModal();
}
function taskTitleHTML(t){return `<button type="button" class="cell-open" data-edit="${t.id}" aria-label="查看或编辑任务 ${esc(t.title)}"><span class="cell-preview">${esc(t.title)}</span><small class="cell-open-hint">查看完整内容 →</small></button>`}
function taskHTML(t){const status=taskStatus(t);return `<tr class="${t.done?'is-done':''}" data-task-id="${t.id}"><td class="completion-cell"><input type="checkbox" aria-label="完成 ${esc(t.title)}" data-toggle="${t.id}" ${t.done?'checked':''}></td><th scope="row" class="task-name">${taskTitleHTML(t)}</th><td class="date-cell">${esc(t.day)}</td><td class="category-cell">${esc(t.category)}</td><td class="number-cell">${t.planned} 分钟</td><td class="time-cell">${taskTimeHTML(t.started_at)}</td><td class="time-cell">${taskTimeHTML(t.finished_at)}</td><td class="number-cell">${actualHTML(t)}</td><td><span class="table-status ${status.style}">${status.label}</span></td><td class="table-actions"><div class="task-action-buttons">${taskTimerButtonHTML(t)}<button type="button" class="link" data-edit="${t.id}">编辑 / 记录</button>${taskMoveButtonHTML(t)}${taskResetButtonHTML(t)}<button type="button" class="link danger" data-delete="${t.id}" aria-label="删除 ${esc(t.title)}">移除</button></div></td></tr>`}


function lessonTrackable(lesson){return lesson&&((lesson.trackable===true)||(lesson.kind==='video'&&lesson.trackable!==false))}
function lessonTitleHTML(lesson){const title=String(lesson.zh_title||lesson.title||'').trim(),original=String(lesson.title||'').trim();return `<span class="lesson-title-text">${esc(title)}</span>${original&&original!==title?`<small class="lesson-original-title">原名：${esc(original)}</small>`:''}`}
function lessonChineseGuideHTML(lesson){const guide=typeof lesson.zh_guide==='string'?lesson.zh_guide.trim():'';return guide?`<details class="lesson-chinese-guide"><summary>中文导学 · 点击展开</summary><small class="lesson-guide-origin">系统原创导学 · 非原课字幕；授课语言以课程标注为准。</small><div class="lesson-guide-content">${esc(guide)}</div></details>`:''}


function aiSchoolTerm(year,semester){return `${year}年级·${semester==='1학기'?'上学期':semester==='2학기'?'下学期':semester}`}
function aiCurriculumComparisonHTML(){
 const data=AI_STUDY_ROUTE,comparison=data.comparison,classification={'전공핵심':'专业核心','전공심화':'专业深化'};
 return `<details class="ai-curriculum-comparison"><summary>两版培养方案对比 · 为什么这样排序？</summary><p>核对版本：${esc(data.basis.join(' 与 '))}，核对日期 ${esc(data.verified_on)}。两版分别列出 ${comparison.old_all_courses} / ${comparison.new_all_courses} 门（含教养课）；${comparison.same_code_courses} 门课程代码相同，不代表学期、学分或内容完全相同。</p><ul class="ai-route-highlights">${data.highlights.map(text=>`<li>${esc(text)}</li>`).join('')}</ul><div class="ai-comparison-table-wrap"><table class="ai-comparison-table"><caption>相同课程代码中的主要变化 · 旧版 → 新版</caption><thead><tr><th scope="col">课程</th><th scope="col">2020–2023</th><th scope="col">2025–2028</th></tr></thead><tbody>${comparison.changes.map(change=>{const course=data.school_courses[change.code]||{};return `<tr><th scope="row">${esc(course.title_zh||course.title_ko||change.code)}<small>${esc(change.code)}</small></th><td>${esc(aiSchoolTerm(change.old_year,change.old_semester))}<small>${esc(change.old_credits)} 学分 · ${esc(classification[change.old_classification]||change.old_classification)}</small></td><td>${esc(aiSchoolTerm(change.new_year,change.new_semester))}<small>${esc(change.new_credits)} 学分 · ${esc(classification[change.new_classification]||change.new_classification)}</small></td></tr>`}).join('')}</tbody></table></div><p>本路线只把现有资源连接到相关知识，不表示一门网课可替代整门校课。中文课名是辅助译名，以韩文与课程代码为准。旧新版开设区间有重叠；插班生适用版本、学分认定、当期开课和选课资格，请向学校确认。</p><a href="${esc(data.source_url)}" target="_blank" rel="noopener noreferrer">汉阳大学官方培养方案 · 切换版本核对 ↗</a></details>`;
}
function aiRoadmapHTML(){
 return `<section class="course-roadmap ai-route" aria-labelledby="aiRouteHeading"><h2 id="aiRouteHeading">人工智能课程学习安排</h2><p>课程分为「现在需要学」和「以后需要学」，各组按学习顺序排列。先补 Python、数学和当前专业课需要的基础，再进入数据分析、机器学习与后续应用。</p><p class="ai-route-scope-note">打开「学习说明」查看每门课学到哪、怎样自测；打开「查看课时」逐节打钩。每天学什么、学多久由你决定，点击「安排任务」加入自己的计划。</p><p class="ai-route-milestone"><strong>12月16日所在周：</strong>学期复盘节点；之后继续根据掌握情况安排学习。</p>${aiCurriculumComparisonHTML()}</section>`;
}
function aiCourseGroupsHTML(courses){
 return [['now','现在需要学','先掌握这些基础。自动驾驶导论当前只学模块概念，完整实践留到相关基础具备之后。'],['later','以后需要学','现在的基础达标后，按下面顺序继续学习；新增课程先放在这里，由你决定何时开始。']].map(([phase,label,note])=>{
  const rows=courses.filter(course=>aiCoursePhase(course)===phase),id=phase==='now'?'aiCoursesNow':'aiCoursesLater';
  return `<section class="ai-course-group" aria-labelledby="${id}"><div class="ai-course-group-heading"><h2 id="${id}">${label}<span>${rows.length} 门</span></h2><p>${note}</p></div>${courseTableHTML(rows,label,'ai')}</section>`;
 }).join('');
}
function courseRoadmapHTML(category){
 if(category==='english')return `<section class="course-roadmap" aria-label="英语系统学习路线"><h2>跟中文视频课，系统提高听说读写</h2><p class="course-roadmap-path">已有四级基础 → Sun老师32节四项课程 → 按薄弱项做练习 → 雅思6.5 / 教授沟通</p><p>主课共8个单元，每单元阅读、写作、听力、口语各一节，没有音标或零基础整套课。先试第一单元四项，熟悉的内容可自测后跳过；每次看课后完成对应练习，并用自己的专业课练提问、问题说明或邮件。原作者公开的32节视频约13小时49分钟，独立练习另计。</p><p>抽查时未登录可选择「360P 流畅」观看；高清、收藏和领取讲义可能需要原平台账号。British Council B2 是英文辅助练习，OpenLearn 是英文图文参考；大连理工MOOC保留为需账号的选学课。雅思6.5需要限时听读、口语录音、写作与反馈共同训练。</p><p class="course-priority-key"><strong>P0 中文四项视频主课</strong> · P2 英文辅助练习 · P3 选学参考 · 每天任务由你安排</p></section>`;
 if(category==='korean')return `<section class="course-roadmap" aria-label="韩语课程与教材练习说明"><h2>你的韩语课程，由你安排</h2><p>下方保留你指定的韩语网站入口。平台课程和视频按原网站的账号与权限访问；系统不代替你安排课程顺序。</p><p>每日练习与课程入口分开：按《延世韩国语》册次选择已核验知识点，练中译韩、韩译中与语法复习；题目为根据知识点编写的原创练习，教材来源在每题下方标明。</p></section>`;
 return aiRoadmapHTML();
}
function courseTableHTML(courses,title,category){const language=category!=='ai';return `<div class="table-scroll" tabindex="0" role="region" aria-label="${esc(title)}课程目录，可左右滚动"><table class="planner-table course-table"><caption>${esc(title)} · 每门课程一行</caption><colgroup><col class="col-number"><col class="col-course-name"><col class="col-source"><col class="col-count"><col class="col-count"><col class="col-description"><col class="col-actions"></colgroup><thead><tr><th scope="col">序号</th><th scope="col">课程名称</th><th scope="col">来源</th><th scope="col">${language?'可完成内容':'视频数'}</th><th scope="col">资料数</th><th scope="col">说明</th><th scope="col">操作</th></tr></thead><tbody>${courses.map((c,i)=>`<tr><td class="number-cell">${i+1}</td><th scope="row" class="course-name-cell"><button type="button" class="course-name-open" data-course-guide="${c.id}" aria-label="查看 ${esc(c.title)} 的学习范围"><span class="course-title-text">${esc(c.title)}</span>${courseAdviceHTML(c)}${courseLanguageHTML(c)}${courseStopHTML(c)}<small class="course-summary-more">${category==='korean'?'查看平台说明':'学习范围与停止点'} →</small></button></th><td><span class="pill ${c.source==='user'?'owned':''}">${c.source==='user'?'我的课程':'补充推荐'}</span>${storageLabel(c)}</td><td class="number-cell">${c.local_key||c.lesson_count?`${language?c.trackable_count===undefined?c.video_count||0:c.trackable_count:c.video_count||0}${language?' '+esc(courseUnitLabel(c)):''}<small class="course-progress">已学 ${c.completed_count||0} ${esc(courseUnitLabel(c))}</small>`:'—'}</td><td class="number-cell">${c.local_key||c.lesson_count?c.lesson_count||0:'—'}</td><td class="course-note-cell"><button type="button" class="course-summary-button" data-course-guide="${c.id}" aria-label="展开 ${esc(c.title)} 的完整学习说明"><span class="table-summary">${esc(courseSummary(c))}</span><span class="course-summary-more">展开说明 →</span></button></td><td><div class="table-actions"><button data-course-guide="${c.id}">学习说明</button>${c.local_key||c.lesson_count?`<button class="primary" data-lessons="${c.id}">查看课时</button>`:''}${onlineCourse(c)}<button data-plan-course="${c.id}">安排任务</button><button class="link" data-edit-course="${c.id}">编辑</button><button class="link danger" data-remove-course="${c.id}">移除</button></div></td></tr>`).join('')||'<tr><td colspan="7" class="empty">这里还没有课程，可以先添加课程名称、链接和说明。</td></tr>'}</tbody></table></div>`;}
function coursesHTML(){
 const category=currentCourseCategory(),language=category!=='ai',title=language?languageLabel(category)+'学习':'课程资料库';
 const courses=(state.courses||[]).map((course,index)=>({course,index})).filter(({course})=>courseCategory(course)===category&&(courseFilter==='all'||course.source===courseFilter)).sort((a,b)=>compareCourseRows(a,b,category)).map(({course})=>course);
 const action=`<div class="language-hero-actions">${language?'<a class="button" href="#languagePractice">每日练习 ↓</a>':''}<button class="primary" id="newCourse">＋ 添加课程</button></div>`;
 return hero(title,language?'课程和练习独立保存，按自己的节奏学习。':'一行一门课程，按需查看资料或安排任务。',action)+courseRoadmapHTML(category)+
 `<div class="toolbar"><div class="segmented">${[['user','我的课程'],['supplement','补充推荐'],['all','全部']].map(([key,label])=>`<button data-course-filter="${key}" class="${courseFilter===key?'selected':''}">${label}</button>`).join('')}</div><div class="storage-toolbar"><span class="muted">${courses.length} 门${language?languageLabel(category):''}课程 / 资源</span><button id="refreshStorage">${language||isCloud()?'刷新目录':'刷新硬盘状态'}</button></div></div>`+
 (language?courseTableHTML(courses,title,category):aiCourseGroupsHTML(courses))+(language?languagePracticeHTML()+(window.StudyListening&&typeof window.StudyListening.html==='function'?window.StudyListening.html(category):''):'');
}
function lessonsHTML(){
 const c=state.courses.find(x=>x.id===openedCourse);if(!c){openedCourse=null;return coursesHTML()}
 const q=lessonQuery.toLowerCase(),visible=lessons.filter(l=>[l.zh_title,l.title,l.section].filter(Boolean).join(' ').toLowerCase().includes(q));
 const trackable=lessons.filter(lessonTrackable),completed=trackable.filter(l=>l.done).length,unit=courseUnitLabel(c);
 return '<button class="link" id="backCourses">← 返回'+(courseCategory(c)==='ai'?'课程库':languageLabel(courseCategory(c))+'学习')+'</button>'+hero(c.title,unit==='模块'?'一行代表多课模块，完成对应课题后再打钩。':unit==='课题'?'按公开课程目录逐课题记录，原平台实际课件以登录后为准。':'一行一份课时资料，学习或安排任务由你选择。',onlineCourse(c))+
 `<section class="card lesson-table-panel">${courseLanguageHTML(c)}<div class="toolbar"><input id="lessonSearch" type="search" aria-label="搜索课时" placeholder="搜索课时或章节，按回车确认" value="${esc(lessonQuery)}"><div class="storage-toolbar"><span class="muted">${visible.length} 份资料</span><button id="refreshStorage">${!c.local_key||isCloud()?'刷新目录':'刷新硬盘状态'}</button></div></div>
 <div class="lesson-progress" role="status" aria-live="polite"><strong>已学完 ${completed} / ${trackable.length} ${esc(unit)}${unit==='节'?(c.local_key?'视频':'课时'):''}</strong><progress value="${completed}" max="${Math.max(1,trackable.length)}" aria-label="课程学习进度"></progress><span>${unit==='模块'?'完成模块内的对应课题后再打钩，可随时取消。':unit==='课题'?'完成对应课题后手动打钩；公开目录条目不等于视频或后台全部课件。':'学完后手动打钩，可随时取消。'}</span></div>
 <p class="storage-notice ${c.local_key&&!localReadable(c)?'offline':''}" role="status">${materialsNote(c)}</p><div id="playerArea" hidden><h3 id="playingTitle"></h3><video id="lessonVideo" controls preload="metadata" playsinline></video><p class="muted">视频由本机 G 盘提供，云端视频存储尚未配置。</p></div>
 <div class="table-scroll" tabindex="0" role="region" aria-label="课时资料表，可左右滚动"><table class="planner-table lesson-table"><caption>${esc(c.title)} · 课时与资料</caption><colgroup><col class="col-completed"><col class="col-number"><col class="col-lesson-name"><col class="col-section"><col class="col-kind"><col class="col-size"><col class="col-lesson-actions"></colgroup><thead><tr><th scope="col">已学完</th><th scope="col">序号</th><th scope="col">课时 / 资料名称</th><th scope="col">章节</th><th scope="col">类型</th><th scope="col">大小 / 位置</th><th scope="col">操作</th></tr></thead><tbody>${visible.map((l,i)=>`<tr class="${lessonTrackable(l)&&l.done?'lesson-done':''}"><td class="lesson-completion-cell">${lessonTrackable(l)?`<input type="checkbox" aria-label="已学完 ${esc(l.zh_title||l.title)}" data-lesson-done="${esc(l.id)}" ${l.done?'checked':''} ${lessonWrites.has(c.id+'/'+l.id)?'disabled':''}>`:'<span class="muted" aria-label="此资料不计入课时进度">—</span>'}</td><td class="number-cell">${i+1}</td><th scope="row" class="lesson-name-cell">${lessonTitleHTML(l)}${lessonTrackable(l)?`<small class="lesson-state ${l.done?'finished':''}">${l.done?'✓ 已学完':'未学完'}</small>`:''}${lessonChineseGuideHTML(l)}</th><td class="lesson-section-cell">${esc(l.section||'课程资料')}</td><td><span class="pill">${esc(l.format||(l.kind==='video'?'视频':l.extension||'资料'))}</span></td><td class="size-cell">${lessonSizeHTML(l)}</td><td><div class="table-actions">${materialAction(c,l)}<button class="link" data-plan-lesson="${esc(l.id)}">安排任务</button></div></td></tr>`).join('')||'<tr><td colspan="7" class="empty">没有匹配的课时。</td></tr>'}</tbody></table></div></section>`;
}
async function saveLessonCompletion(box){
 const courseId=openedCourse,lessonId=box.dataset.lessonDone,key=courseId+'/'+lessonId;
 const lesson=lessons.find(l=>l.id===lessonId);
 if(!lesson||lessonWrites.has(key)){box.checked=!!(lesson&&lesson.done);return}
 const checked=box.checked,previous=!!lesson.done;
 lessonWrites.add(key);box.disabled=true;
 try{
  const result=await api('/courses/'+courseId+'/lessons/'+encodeURIComponent(lessonId),'PATCH',{done:checked});
  const course=state&&state.courses.find(c=>c.id===courseId);
  if(openedCourse===courseId){const current=lessons.find(l=>l.id===lessonId);if(current)current.done=result.done;if(course)course.completed_count=lessons.filter(l=>lessonTrackable(l)&&l.done).length}
  else if(course)course.completed_count=result.completed_count;
  const label=courseUnitLabel(course)==='模块'?'这个模块':courseUnitLabel(course)==='课题'?'这个课题':'这节课';toast(label+(result.done?'已标记为学完':'已恢复为未学完'))
 }catch(e){if(box.isConnected)box.checked=previous;toast(e.message)}
 finally{lessonWrites.delete(key);if(state&&isCourseView()&&openedCourse===courseId)render();else if(box.isConnected)box.disabled=false}
}


function isLanguageView(){return view==='english'||view==='korean'}
function isCourseView(){return view==='courses'||isLanguageView()}
function courseCategory(course){return ['english','korean'].includes(course&&course.category)?course.category:'ai'}
function currentCourseCategory(){return isLanguageView()?view:'ai'}
function languageLabel(language){return language==='korean'?'韩语':'英语'}
function languageConfig(){
 const language=isLanguageView()?view:'english',saved=state&&state.language_settings&&state.language_settings[language]||{},mode=languageModes[language]||'daily';
 return {language,level:mode==='history'?'all':String(saved.level||(language==='korean'?'1':'B2')),direction:['mixed','to_target','to_zh'].includes(saved.direction)?saved.direction:'mixed',day:languageDays[language]||today(),mode,page:languageHistoryPages[language]||1};
}
function languageCacheKey(config){return (config.mode==='history'?[state&&state.user.username,config.language,'history',config.page||1]:[state&&state.user.username,config.language,config.level,config.day,config.direction,config.mode]).join('|')}
function languageCache(create=true){
 if(!state||!isLanguageView())return null;const config=languageConfig(),key=languageCacheKey(config);
 if(!languageCaches.has(key)&&create)languageCaches.set(key,{key,config,items:[],summary:{},loaded:false,loading:false,error:'',notice:'',generation:0});
 return languageCaches.get(key)||null;
}
function languageItemDirty(item){return !!item&&!!item._saved&&(item.answer!==item._saved.answer||!!item.completed!==!!item._saved.completed||!!item.review!==!!item._saved.review)}
function languageCacheDirty(cache){return !!cache&&cache.items.some(languageItemDirty)}
function languageHasDirty(){return Array.from(languageCaches.values()).some(languageCacheDirty)}
function languageWriteKey(cache,item){return [cache.key,item.language,item.level,item.day,item.practice_direction,item.id].join('|')}
function languageBusy(cache){return !!cache&&((languageBatch&&languageBatch.key===cache.key)||cache.items.some(item=>languageWrites.has(languageWriteKey(cache,item))))}
function languageActiveCache(cache){return !!state&&isLanguageView()&&!openedCourse&&languageCacheKey(languageConfig())===cache.key}
function languageRowBusy(cache,item){return languageSettingBusy||languageWrites.has(languageWriteKey(cache,item))||!!(languageBatch&&languageBatch.key===cache.key)}
function languageCanLeave(){if(languageBatch){toast('正在批改练习，请稍候。');return false}if(languageSettingBusy){toast('正在保存练习设置，请稍候。');return false}if(languageHasDirty()){toast('还有未保存的句子，请先保存练习或撤销更改。');return false}if(languageWrites.size){toast('正在保存练习，请稍候。');return false}return !window.StudyListening||typeof window.StudyListening.beforeLeave!=='function'||window.StudyListening.beforeLeave()}
function languageNormaliseItem(raw,config){
 const item={...raw,language:raw.language||config.language,level:String(raw.level||config.level),day:raw.day||config.day,practice_direction:raw.practice_direction||config.direction,answer:String(raw.answer||''),completed:!!raw.completed,review:!!raw.review,_revision:0};
 item.grading=languageValidGrading(raw.grading,item.answer);item.manual_review=item.grading?languageValidManualReview(raw.manual_review,item.answer):null;item._gradingStale=false;item._saved={answer:item.answer,completed:item.completed,review:item.review,updated_at:item.updated_at,grading:item.grading,manual_review:item.manual_review};return item;
}
function languageDirectionLabel(direction,language){const target=language==='korean'?'韩':'英';return direction==='to_zh'?target+'译中':'中译'+target}
function languageReference(value){if(!value)return '';if(typeof value==='string')return value;if(Array.isArray(value))return value.map(languageReference).filter(Boolean).join('；');return ['book','volume','unit','lesson','page','source','label'].filter(key=>value[key]!==undefined).map(key=>String(value[key])).join(' · ')}
async function loadLanguagePractice(){
 const cache=languageCache();if(!cache||cache.loaded||cache.loading)return;
 const owner=state.user.username,request=++cache.generation,config=cache.config;cache.loading=true;cache.error='';
 const query=new URLSearchParams(config.mode==='history'?{language:config.language,mode:'history',page:config.page||1,page_size:20}:{language:config.language,level:config.level,...(config.mode==='review'?{}:{day:config.day,direction:config.direction})});
 try{
  const data=await api('/language-practice'+(config.mode==='review'?'/review':'')+'?'+query);
  if(!state||state.user.username!==owner||request!==cache.generation)return;
  if(languageCacheDirty(cache))return;
  cache.items=(Array.isArray(data.items)?data.items:[]).map(item=>languageNormaliseItem(item,config));cache.summary=data.summary||{};cache.notice=String(data.notice||'');cache.level_label=String(data.level_label||'');cache.total=Math.max(0,Number(data.total)||0);cache.page=Math.max(1,Number(data.page)||config.page||1);cache.page_size=Math.max(1,Number(data.page_size)||20);cache.has_more=data.has_more===true;cache.loaded=true;
 }catch(error){if(state&&state.user.username===owner&&request===cache.generation){cache.error=error.message||'练习暂时无法加载';cache.loaded=true}}
 finally{if(request===cache.generation){cache.loading=false;if(state&&state.user.username===owner&&isLanguageView()&&languageCacheKey(languageConfig())===cache.key)render()}}
}
async function refreshLanguagePractice(cache,nextGroup=false){
 if(!languageActiveCache(cache)||cache.loading||languageBusy(cache)||languageSettingBusy)return;
 if(!languageCanLeave()){
  cache.feedback=languageCacheDirty(cache)?'还有未保存的句子。请先点「保存全部练习」，再换题或同步。':'当前还有未保存内容或正在处理的操作，请先完成后再刷新。';
  const summary=$('#practiceSummary');if(summary)summary.textContent=languageSummaryText(cache);return;
 }
 const owner=state.user.username,request=++cache.generation,config=cache.config;
 cache.loading=true;languageSettingBusy=true;cache.error='';cache.feedback=nextGroup?'正在切换下一组题…':'正在同步练习进度…';render();
 try{
  const query=new URLSearchParams(config.mode==='history'?{language:config.language,mode:'history',page:config.page||1,page_size:20}:{language:config.language,level:config.level,...(config.mode==='review'?{}:{day:config.day,direction:config.direction})});
  const data=nextGroup?await api('/language-practice/next','POST',{language:config.language,level:config.level,day:config.day,direction:config.direction}):await api('/language-practice'+(config.mode==='review'?'/review':'')+'?'+query);
  if(!state||state.user.username!==owner||request!==cache.generation)return;
  if(languageCacheDirty(cache)){cache.feedback='发现新修改，已保留你的草稿；请先保存再刷新。';return}
  cache.items=(Array.isArray(data.items)?data.items:[]).map(item=>languageNormaliseItem(item,config));cache.summary=data.summary||{};cache.notice=String(data.notice||'');cache.level_label=String(data.level_label||'');cache.total=Math.max(0,Number(data.total)||0);cache.page=Math.max(1,Number(data.page)||config.page||1);cache.page_size=Math.max(1,Number(data.page_size)||20);cache.has_more=data.has_more===true;cache.loaded=true;
  cache.feedback=nextGroup?String(data.refresh_message||'已切换下一组题；已保存答案仍在练习记录。'):'已同步最新进度。换题请点「换一组题」；已保存的答案会保留。';
 }catch(error){if(state&&state.user.username===owner&&request===cache.generation){cache.error=error.message||'练习刷新失败';cache.feedback='本次未能刷新，原题目和答案仍保留；请重试。'}}
 finally{languageSettingBusy=false;if(request===cache.generation){cache.loading=false;if(state&&state.user.username===owner&&languageActiveCache(cache))render()}}
}
function languageLevelOptions(config){
 const levels=config.language==='korean'?['1','2','3','4','5','6']:['A1','A2','B1','B2','C1'];
 return levels.map(level=>`<option value="${level}" ${level===config.level?'selected':''}>${config.language==='korean'?'延世韩国语 · 第 '+level+' 册':level+' · '+({A1:'基础表达',A2:'日常交流',B1:'独立沟通',B2:'讨论与听课',C1:'学术表达'}[level])}</option>`).join('');
}
function languageValidGrading(grading,answer){
 return grading&&typeof grading==='object'&&['correct','incorrect','needs_review'].includes(grading.status)&&typeof grading.graded_answer==='string'&&grading.graded_answer===String(answer)?grading:null;
}
function languageValidManualReview(review,answer){return review&&typeof review==='object'&&['correct','incorrect'].includes(review.status)&&typeof review.reviewed_answer==='string'&&review.reviewed_answer===String(answer)?review:null}
function languageManualReviewHTML(item,cache){
 const review=languageValidManualReview(item.manual_review,item.answer),index=cache.items.indexOf(item),busy=languageRowBusy(cache,item),ready=item.answer===item._saved.answer&&!item._gradingStale,stamp=new Date(review&&review.reviewed_at||'');
 return `<section class="practice-manual-review${review?' has-judgment is-'+review.status:''}" aria-label="我的判断"><strong>${review?'我的判断：'+(review.status==='correct'?'✓ 正确':'✗ 错误'):'我的判断：尚未确认'}</strong><p>${review?'这是你保存的人工判断，上方自动核对结果继续保留。':'自动核对不能确定其他译法时，你可以结合教材、参考表达或老师反馈，自行确认对错。'}</p>${index>=0?`<div class="practice-manual-actions"><button type="button" data-practice-manual="${index}" data-manual-result="correct" aria-pressed="${!!review&&review.status==='correct'}" ${busy||!ready||review&&review.status==='correct'?'disabled':''}>我确认正确</button><button type="button" data-practice-manual="${index}" data-manual-result="incorrect" aria-pressed="${!!review&&review.status==='incorrect'}" ${busy||!ready||review&&review.status==='incorrect'?'disabled':''}>我确认错误</button>${review?`<button type="button" class="link" data-practice-manual="${index}" data-manual-result="clear" ${busy||!ready?'disabled':''}>撤销人工判断</button>`:''}</div>`:''}${Number.isFinite(stamp.getTime())?`<small>确认于 ${esc(stamp.toLocaleString('zh-CN',{hour12:false}))}</small>`:''}<small>人工判断单独保存，不会自动勾选完成或复习。</small></section>`;
}
function languageGradingHTML(item,cache){
 const key=languageWriteKey(cache,item);
 if(languageGradeWrites.has(key))return `<p class="practice-grade-pending">${languageGradeWrites.get(key)===item.answer?'正在核对并保存本次答案…':'正在核对已提交版本；当前新修改的答案尚未批改。'}</p>`;
 const grading=languageValidGrading(item.grading,item.answer);
 if(item._gradingStale||(item.grading&&!grading))return '<p class="practice-grade-stale">答案已修改，原批改和人工判断已失效；请重新批改。</p>';
 if(!grading)return '<small class="practice-grade-empty">填写答案后可点击「批改本题」。</small>';
 const labels={correct:'正确',incorrect:'需要修改',needs_review:'需复核'},icons={correct:'✓',incorrect:'!',needs_review:'?'},label=labels[grading.status];
 const manual=languageValidManualReview(item.manual_review,item.answer),visualStatus=manual?manual.status:grading.status;
 const feedback=Array.isArray(grading.feedback)?grading.feedback.filter(value=>typeof value==='string'&&value.trim()):[],stamp=new Date(grading.graded_at||'');
 return `<section class="practice-grade-result is-${visualStatus}" aria-label="批改结果：${label}"><div class="practice-grade-heading"><strong><span aria-hidden="true">${icons[grading.status]}</span> ${label}</strong><small>基础自动核对</small></div>${feedback.length?`<ul>${feedback.map(value=>`<li>${esc(value)}</li>`).join('')}</ul>`:''}${typeof grading.reference_answer==='string'&&grading.reference_answer?`<details class="practice-grade-reference"><summary>参考表达</summary><p>${esc(grading.reference_answer)}</p><small>参考表达不是唯一正确译法。</small></details>`:''}${Number.isFinite(stamp.getTime())?`<small class="practice-grade-time">批改于 ${esc(stamp.toLocaleString('zh-CN',{hour12:false}))}</small>`:''}${languageManualReviewHTML(item,cache)}</section>`;
}
function languageSummaryText(cache){
 const done=cache.items.filter(item=>item._saved.completed).length,dirty=cache.items.filter(languageItemDirty).length;
 return (cache.config.mode==='history'?`练习记录共 ${cache.total||0} 条 · 本页 ${cache.items.length} 条 · 本页完成 ${done} 题`:(cache.config.mode==='review'?'复习列表':'已保存完成')+' '+done+' / '+cache.items.length+' 题')+(dirty?' · '+dirty+' 题尚未保存':'')+(cache.feedback?' · '+cache.feedback:'');
}
function languageHistoryPagerHTML(cache){
 if(cache.config.mode!=='history'||!cache.loaded||cache.loading||cache.error)return '';
 const page=cache.page||cache.config.page||1,pages=Math.max(1,Math.ceil((cache.total||0)/(cache.page_size||20))),busy=languageBusy(cache)||languageSettingBusy;
 return `<div class="practice-history-pagination" role="navigation" aria-label="练习记录分页"><span role="status">共 ${cache.total||0} 条 · 第 ${page} / ${pages} 页 · 每页 ${cache.page_size||20} 条</span><div><button type="button" id="practiceHistoryPrevious" ${busy||page<=1?'disabled':''}>上一页</button><button type="button" id="practiceHistoryNext" ${busy||!cache.has_more?'disabled':''}>下一页</button></div></div>`;
}
function languagePracticeHTML(){
 const cache=languageCache(),config=cache.config,language=languageLabel(config.language),items=cache.items,done=items.filter(item=>item._saved.completed).length,dirty=items.filter(languageItemDirty).length,busy=languageBusy(cache)||languageSettingBusy||cache.loading,target=config.language==='korean'?'韩文':'英文';
 return `<section id="languagePractice" class="card language-practice" aria-label="${language}每日翻译与语法复习"><div class="language-section-heading"><div><span class="eyebrow">${config.language==='korean'?'YONSEI · GRAMMAR REVIEW':'ENGLISH · DAILY PRACTICE'}</span><h2>每日翻译与语法复习</h2><p class="muted">${config.language==='korean'?'选择教材册次，按已核验的教材知识点做原创翻译与造句练习。':'按自己的难度练习表达，重点用于课堂提问、请求解释和向教授汇报。'}每天的练习在这里更新，由你决定何时完成。</p></div><div class="practice-heading-actions"><button id="gradeAllPractice" class="primary" ${busy||!items.some(item=>String(item.answer).trim())?'disabled':''}>批改已填写</button><button id="saveAllPractice" ${!dirty||busy?'disabled':''}>保存全部练习</button></div></div><div class="language-practice-controls"><label>${config.language==='korean'?'教材册次':'英语难度'}<select id="practiceLevel" ${busy||config.mode==='history'?'disabled':''}>${config.mode==='history'?'<option value="all">所有级别 / 册次</option>':languageLevelOptions(config)}</select></label><label>练习日期<input id="practiceDay" type="date" value="${esc(config.day)}" ${busy||config.mode!=='daily'?'disabled':''}></label><label>翻译方向<select id="practiceDirection" ${busy||config.mode!=='daily'?'disabled':''}><option value="mixed" ${config.direction==='mixed'?'selected':''}>双向交替</option><option value="to_target" ${config.direction==='to_target'?'selected':''}>中文 → ${target}</option><option value="to_zh" ${config.direction==='to_zh'?'selected':''}>${target} → 中文</option></select></label><div class="language-practice-switch"><button id="practiceDaily" class="${config.mode==='daily'?'selected':''}" ${busy?'disabled':''}>每日练习</button><button id="practiceReview" class="${config.mode==='review'?'selected':''}" ${busy?'disabled':''}>需复习的句子</button><button id="practiceHistory" class="${config.mode==='history'?'selected':''}" ${busy?'disabled':''}>练习记录</button>${config.mode==='daily'?`<button id="nextPractice" class="primary" ${busy?'disabled':''}>换一组题</button>`:''}<button id="refreshPractice" ${busy?'disabled':''}>${config.mode==='daily'?'同步进度':'刷新记录'}</button></div></div><p class="language-practice-summary" id="practiceSummary" role="status">${languageSummaryText(cache)}</p><p class="language-practice-notice">写好答案后点「批改本题」，或用「批改已填写」逐题核对并保存。基础自动核对会将可确定的结果判为正确或需要修改，其他译法标为需复核。参考表达不是唯一答案；你也可以在结果下方确认自己的对错判断。完成与复习仍由你勾选。保存或批改后的非空答案会进入「练习记录」，可跨日期和级别回看。</p>${cache.notice?`<p class="language-source-notice">${esc(cache.notice)}</p>`:''}${cache.error?`<p class="language-load-error" role="alert">${esc(cache.error)}。可点击「同步进度」或「刷新记录」重试。</p>`:cache.loading||!cache.loaded?'<p class="language-loading" role="status">正在载入练习…</p>':!items.length?`<p class="language-empty">${config.mode==='history'?'还没有已保存的非空答案。写好后点保存或批改，就能在这里回看。':config.mode==='review'?'当前级别还没有标记需要复习的句子。':cache.notice?'当前选择暂无可用练习，请按上方说明选择教材或稍后刷新。':'当前日期暂无练习。请调整日期或级别后重试。'}</p>`:`<div class="table-scroll" tabindex="0" role="region" aria-label="${language}翻译练习表，可左右滚动"><table class="planner-table language-practice-table"><caption>${config.mode==='history'?'练习记录 · 所有级别 / 日期':config.mode==='review'?'需复习的句子':config.day+' · '+(cache.level_label||(config.language==='korean'?'延世韩国语第 '+config.level+' 册':config.level))} · ${items.length} 题</caption><colgroup><col class="practice-col-number"><col class="practice-col-grammar"><col class="practice-col-prompt"><col class="practice-col-answer"><col class="practice-col-reference"><col class="practice-col-actions"></colgroup><thead><tr><th scope="col">序号</th><th scope="col">知识点</th><th scope="col">题目</th><th scope="col">我的译文 / 句子</th><th scope="col">参考与自检</th><th scope="col">保存、批改与复习</th></tr></thead><tbody>${items.map((item,index)=>languagePracticeRowHTML(item,index,cache)).join('')}</tbody></table></div>`}${languageHistoryPagerHTML(cache)}</section>`;
}
function languagePracticeRowHTML(item,index,cache){
 const label=languageDirectionLabel(item.direction,item.language),reference=languageReference(item.reference),key=languageWriteKey(cache,item),writing=languageWrites.has(key),grading=languageGradeWrites.has(key),busy=languageRowBusy(cache,item),dirty=languageItemDirty(item),sourceLang=item.direction==='to_zh'?(item.language==='korean'?'ko':'en'):'zh-CN';
 return `<tr data-practice-row="${index}" class="${item._saved.completed?'practice-finished':''}"><td>${index+1}</td><th scope="row"><strong>${esc(item.knowledge_point||'表达练习')}</strong>${reference?`<small class="practice-reference">${esc(reference)}</small>`:''}${cache.config.mode!=='daily'?`<small class="practice-reference">原练习日期：${esc(item.day)}${cache.config.mode==='history'?' · '+(item.language==='korean'?'第 '+esc(item.level)+' 册':esc(item.level)):''}</small>`:''}${item.explanation?`<details class="practice-grammar"><summary>语法提示</summary><p>${esc(item.explanation)}</p></details>`:''}</th><td><span class="pill">${esc(label)}</span><p>${esc(item.prompt||'请根据指定知识点完成翻译。')}</p><blockquote lang="${sourceLang}" class="practice-source">${esc(item.source_text||'')}</blockquote>${item.hint?`<small class="muted">${esc(item.hint)}</small>`:''}</td><td><label class="practice-visually-hidden" for="practiceAnswer${index}">第 ${index+1} 题我的答案</label><textarea id="practiceAnswer${index}" data-practice-answer="${index}" ${languageSettingBusy?'disabled':''} rows="6" maxlength="4000" placeholder="先写下自己的译文或句子，再查看参考表达。">${esc(item.answer)}</textarea><small class="practice-save-state" data-practice-status="${index}" role="status">${writing?grading?'正在批改并保存…':'正在保存…':dirty?'尚未保存':item.updated_at?'已保存到账号':'尚未填写'}</small><div class="practice-grade-panel" data-practice-grading="${index}" role="status" aria-live="polite" aria-atomic="true">${languageGradingHTML(item,cache)}</div></td><td><details class="practice-model"><summary>查看参考译文与自检</summary><p class="practice-model-answer" lang="${item.direction==='to_zh'?'zh-CN':item.language==='korean'?'ko':'en'}">${esc(item.model_answer||'请结合语法提示自查，也可以向老师确认表达。')}</p>${Array.isArray(item.checklist)&&item.checklist.length?`<ul>${item.checklist.map(point=>`<li>${esc(point)}</li>`).join('')}</ul>`:''}<small class="muted">原创参考表达，不是唯一答案。</small></details></td><td><div class="practice-row-actions"><label class="practice-check"><input type="checkbox" data-practice-complete="${index}" aria-label="完成第 ${index+1} 题" ${item.completed?'checked':''} ${busy?'disabled':''}>已完成</label><label class="practice-check"><input type="checkbox" data-practice-review="${index}" aria-label="第 ${index+1} 题需要复习" ${item.review?'checked':''} ${busy?'disabled':''}>需要复习</label><button type="button" class="primary" data-practice-grade="${index}" aria-label="批改第 ${index+1} 题" ${busy||!String(item.answer).trim()?'disabled':''}>${grading?'正在批改…':'批改本题'}</button><button type="button" data-practice-save="${index}" ${!dirty||busy?'disabled':''}>保存本题</button><button type="button" class="link" data-practice-reset="${index}" ${!dirty||busy?'disabled':''}>撤销未保存</button></div></td></tr>`;
}
function updateLanguagePracticeControls(cache){
 if(!languageActiveCache(cache))return;
 const busy=languageBusy(cache)||languageSettingBusy,totalDirty=cache.items.filter(languageItemDirty).length,totalDone=cache.items.filter(value=>value._saved.completed).length;
 if($('#practiceSummary'))$('#practiceSummary').textContent=languageSummaryText(cache);
 if($('#saveAllPractice'))$('#saveAllPractice').disabled=!totalDirty||busy;
 const gradeAll=$('#gradeAllPractice');if(gradeAll){gradeAll.disabled=busy||!cache.items.some(item=>String(item.answer).trim());gradeAll.textContent=languageBatch&&languageBatch.key===cache.key?`正在批改 ${languageBatch.done} / ${languageBatch.total}`:'批改已填写'}
 for(const id of ['practiceLevel','practiceDay','practiceDirection','practiceDaily','practiceReview','practiceHistory','refreshPractice','nextPractice']){const control=$('#'+id);if(control)control.disabled=busy||cache.loading||cache.config.mode==='history'&&id==='practiceLevel'||cache.config.mode!=='daily'&&['practiceDay','practiceDirection'].includes(id)}
 const previous=$('#practiceHistoryPrevious'),next=$('#practiceHistoryNext');if(previous)previous.disabled=busy||(cache.page||cache.config.page||1)<=1;if(next)next.disabled=busy||!cache.has_more;
}
function updateLanguageDraftUI(cache,index){
 if(!languageActiveCache(cache))return;
 const item=cache.items[index],row=$(`[data-practice-row="${index}"]`);if(!item||!row)return;
 const key=languageWriteKey(cache,item),writing=languageWrites.has(key),grading=languageGradeWrites.has(key),busy=languageRowBusy(cache,item),dirty=languageItemDirty(item),status=row.querySelector('[data-practice-status]');
 row.setAttribute('aria-busy',writing?'true':'false');if(status)status.textContent=writing?grading?'正在批改并保存…':'正在保存…':dirty?'尚未保存':item.updated_at?'已保存到账号':'尚未填写';
 const saveButton=row.querySelector('[data-practice-save]');if(saveButton)saveButton.textContent=writing&&!grading?'正在保存…':'保存本题';
 row.querySelectorAll('[data-practice-save],[data-practice-reset]').forEach(button=>button.disabled=busy||!dirty);row.querySelectorAll('input[type="checkbox"]').forEach(box=>box.disabled=busy);row.classList.toggle('practice-finished',!!item._saved.completed);
 const gradeButton=row.querySelector('[data-practice-grade]');if(gradeButton){gradeButton.disabled=busy||!String(item.answer).trim();gradeButton.textContent=grading?'正在批改…':'批改本题'}
 const panel=row.querySelector('[data-practice-grading]'),gradeHTML=languageGradingHTML(item,cache);if(panel&&panel._gradeHTML!==gradeHTML){panel.innerHTML=gradeHTML;panel._gradeHTML=gradeHTML}
 updateLanguagePracticeControls(cache);
}
async function saveLanguageItem(cache,index,{quiet=false,grade=false,manualResult=undefined}={}){
 const item=cache.items[index];if(!state||!item||cache.key!==languageCacheKey(cache.config))return false;const key=languageWriteKey(cache,item);if(languageWrites.has(key))return false;
 const manual=manualResult!==undefined;
 if(manual&&(!['correct','incorrect',null].includes(manualResult)||!languageValidGrading(item.grading,item.answer)||item._gradingStale||item.answer!==item._saved.answer)){if(!quiet)toast('请先批改并保存当前答案，再确认自己的判断。');return false}
 if(grade&&!String(item.answer).trim()){if(!quiet)toast('请先填写答案，再批改。');return false}
 if(item.completed&&!String(item.answer).trim()){toast('先写下自己的答案，再标记完成。');item.completed=!!item._saved.completed;const box=$(`[data-practice-complete="${index}"]`);if(box)box.checked=item.completed;updateLanguageDraftUI(cache,index);return false}
 const owner=state.user.username,revision=item._revision,submitted={language:item.language,level:item.level,day:item.day,direction:item.practice_direction,exercise_id:item.id,answer:item.answer,completed:!!item.completed,review:!!item.review,...(grade?{grade:true}:{}),...(manual?{manual_result:manualResult}:{})};
 languageWrites.add(key);if(grade)languageGradeWrites.set(key,submitted.answer);updateLanguageDraftUI(cache,index);
 try{
  const result=await api('/language-practice','PATCH',submitted);if(!state||state.user.username!==owner||cache.key!==languageCacheKey(cache.config))return false;
  const saved=result.item||submitted,savedAnswer=String(saved.answer===undefined?submitted.answer:saved.answer),savedGrading=languageValidGrading(saved.grading===undefined?item._saved.grading:saved.grading,savedAnswer);
  const savedManual=savedGrading?languageValidManualReview(saved.manual_review===undefined?item._saved.manual_review:saved.manual_review,savedAnswer):null;
  item._saved={answer:savedAnswer,completed:saved.completed===undefined?submitted.completed:!!saved.completed,review:saved.review===undefined?submitted.review:!!saved.review,updated_at:saved.updated_at||new Date().toISOString(),grading:savedGrading,manual_review:savedManual};item.updated_at=item._saved.updated_at;
  item.grading=savedGrading;item.manual_review=savedManual;
  if(item._revision===revision){item.answer=item._saved.answer;item.completed=item._saved.completed;item.review=item._saved.review;if(grade)item._gradingStale=false}
  else if(grade||savedGrading)item._gradingStale=true;
  for(const other of languageCaches.values())if(other!==cache&&other.config.language===item.language&&!languageCacheDirty(other))other.loaded=false;
  if(!quiet)toast(item._revision===revision?(manual?(manualResult===null?'已撤销人工判断。':'我的判断已保存：'+(manualResult==='correct'?'正确':'错误')+'。'):grade?'批改结果和答案已保存。':'练习已保存，其他设备登录后可继续复习。'):'已保存刚才提交的版本；新修改的内容还需再次保存或批改。');return true;
 }catch(error){if(state&&state.user.username===owner)toast(error.message||'操作失败，输入内容仍保留。');return false}
 finally{languageWrites.delete(key);languageGradeWrites.delete(key);updateLanguageDraftUI(cache,index)}
}
async function gradeLanguageBatch(cache){
 if(!languageActiveCache(cache)||languageBusy(cache)||languageSettingBusy)return;
 const owner=state.user.username,plan=cache.items.map((item,index)=>({index,revision:item._revision})).filter(entry=>String(cache.items[entry.index].answer).trim());
 if(!plan.length){toast('请先填写至少一道题的答案。');return}
 const batch={key:cache.key,owner,done:0,total:plan.length};languageBatch=batch;cache.items.forEach((item,index)=>updateLanguageDraftUI(cache,index));
 let graded=0,failed=0,changed=0;
 try{
  for(const entry of plan){
   if(languageBatch!==batch||!state||state.user.username!==owner||!languageActiveCache(cache))break;
   const item=cache.items[entry.index];
   if(item._revision!==entry.revision){changed++;batch.done++;updateLanguagePracticeControls(cache);continue}
   const ok=await saveLanguageItem(cache,entry.index,{quiet:true,grade:true});
   if(ok){if(item._revision===entry.revision)graded++;else changed++}else failed++;
   batch.done++;updateLanguagePracticeControls(cache);
  }
  if(state&&state.user.username===owner&&languageActiveCache(cache))toast(`已批改 ${graded} 题。${changed?' '+changed+' 题有新修改，请重新批改。':''}${failed?' '+failed+' 题未成功，答案仍保留。':''}`);
 }finally{if(languageBatch===batch)languageBatch=null;cache.items.forEach((item,index)=>updateLanguageDraftUI(cache,index))}
}
async function changeLanguageSetting(key,value){
 const previous=languageConfig();if(!languageCanLeave()){render();return}
 const language=previous.language,next={level:previous.level,direction:previous.direction,[key]:value};languageSettingBusy=true;render();
 try{await api('/language-settings','PATCH',{language,...next});if(!state||!isLanguageView()||view!==language)return;if(!state.language_settings)state.language_settings={};state.language_settings[language]=next}
 catch(error){toast(error.message)}finally{languageSettingBusy=false;if(state&&view===language)render()}
}
function bindLanguagePractice(){
 if(!isLanguageView()||openedCourse)return;const cache=languageCache();
 const on=(selector,event,handler)=>{const element=$(selector);if(element)element.addEventListener(event,handler)};
 on('#practiceLevel','change',event=>changeLanguageSetting('level',event.target.value));on('#practiceDirection','change',event=>changeLanguageSetting('direction',event.target.value));
 on('#practiceDay','change',event=>{if(!event.target.value){event.target.value=cache.config.day;return}if(!languageCanLeave()){event.target.value=cache.config.day;return}languageDays[view]=event.target.value;render()});
 const setMode=mode=>{if(!languageCanLeave())return;if(mode==='history'&&languageModes[view]!=='history')languageHistoryPages[view]=1;languageModes[view]=mode;const existing=languageCache();if(existing)existing.loaded=false;render()};on('#practiceDaily','click',()=>setMode('daily'));on('#practiceReview','click',()=>setMode('review'));on('#practiceHistory','click',()=>setMode('history'));
 const changeHistoryPage=delta=>{if(cache.config.mode!=='history'||delta>0&&!cache.has_more||!languageCanLeave())return;languageHistoryPages[view]=Math.max(1,(cache.page||cache.config.page||1)+delta);const nextCache=languageCache();if(nextCache)nextCache.loaded=false;render()};on('#practiceHistoryPrevious','click',()=>changeHistoryPage(-1));on('#practiceHistoryNext','click',()=>changeHistoryPage(1));
 on('#refreshPractice','click',()=>refreshLanguagePractice(cache));
 on('#nextPractice','click',()=>refreshLanguagePractice(cache,true));
 document.querySelectorAll('[data-practice-answer]').forEach(input=>input.addEventListener('input',()=>{const index=+input.dataset.practiceAnswer,item=cache.items[index];if(item){if(item.grading)item._gradingStale=true;item.answer=input.value;item._revision++;updateLanguageDraftUI(cache,index)}}));
 document.querySelectorAll('[data-practice-complete],[data-practice-review]').forEach(box=>box.addEventListener('change',async()=>{const complete=box.dataset.practiceComplete!==undefined,index=+(complete?box.dataset.practiceComplete:box.dataset.practiceReview),item=cache.items[index];if(!item)return;item[complete?'completed':'review']=box.checked;item._revision++;await saveLanguageItem(cache,index)}));
 document.querySelectorAll('[data-practice-save]').forEach(button=>button.onclick=()=>saveLanguageItem(cache,+button.dataset.practiceSave));
 document.querySelectorAll('[data-practice-grade]').forEach(button=>button.onclick=()=>saveLanguageItem(cache,+button.dataset.practiceGrade,{grade:true}));
 document.querySelectorAll('[data-practice-grading]').forEach(panel=>panel.addEventListener('click',event=>{const button=event.target.closest('[data-practice-manual]');if(!button||button.disabled||!panel.contains(button)||!languageActiveCache(cache))return;saveLanguageItem(cache,+button.dataset.practiceManual,{manualResult:button.dataset.manualResult==='clear'?null:button.dataset.manualResult})}));
 on('#gradeAllPractice','click',()=>gradeLanguageBatch(cache));
 document.querySelectorAll('[data-practice-reset]').forEach(button=>button.onclick=()=>{const index=+button.dataset.practiceReset,item=cache.items[index];if(!item)return;Object.assign(item,{answer:item._saved.answer,completed:item._saved.completed,review:item._saved.review,grading:item._saved.grading,manual_review:item._saved.manual_review,_gradingStale:false});item._revision++;render()});
 on('#saveAllPractice','click',async()=>{let saved=0,failed=0;for(let index=0;index<cache.items.length;index++){if(languageItemDirty(cache.items[index])){if(await saveLanguageItem(cache,index,{quiet:true}))saved++;else failed++}}toast(failed?`已保存 ${saved} 题，另有 ${failed} 题未保存，输入内容仍保留。`:`已保存 ${saved} 题练习。`)});
}

function render(){
 if(!state)return;
 const calendarDay=today(),mainDay=normaliseMainDay();
 const carriedDraft=mainDay!==selected||calendarDay!==renderedCalendarDay?quickAddDraft():null;
 selected=mainDay;
 renderedCalendarDay=calendarDay;
 if(window.StudyListening&&typeof window.StudyListening.dispose==='function')window.StudyListening.dispose();const main=$('#app main');if(main)main.dataset.view=view;const names={today:'每日计划',courses:'课程资料库',english:'英语学习',korean:'韩语学习',journal:'任务记录',settings:'个人设置'};$('#breadcrumb').textContent='人工智能学习计划 / '+names[view];document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.view===view));let html='';const all=state.tasks||[],tasks=all.filter(t=>t.day===selected).sort((a,b)=>a.done-b.done||a.id-b.id),done=tasks.filter(t=>t.done).length;
if(view==='today'){const rest=isRestDay(),shown=tasks.filter(t=>filter==='all'||(filter==='done'?t.done:!t.done));html=hero(`${state.user.name}，今天想完成什么？`,'由你安排任务。点「开始计时」记录时间，完成后打钩。','<button class="primary" id="newTask">＋ 新建任务</button>')+taskMoveNoticeHTML()+`<div class="stats"><div class="stat"><span>当天已完成</span><strong>${done} <small>/ ${tasks.length}</small></strong><span>按照自己的节奏来</span></div><div class="stat"><span>当天计划用时</span><strong>${tasks.reduce((s,t)=>s+t.planned,0)} <small>分钟</small></strong><span>由你添加的任务决定</span></div><div class="stat actual-stat"><span>当天实际投入 <small>（自动计算）</small></span><strong id="settledActualTotal">${formatTaskDuration(tasks.reduce((sum,t)=>sum+settledTaskMilliseconds(t),0))}</strong><span>结束减开始并扣除暂停，未结束的任务完成后计入</span></div><div class="stat"><span>累计完成</span><strong>${all.filter(t=>t.done).length} <small>项</small></strong><span>历史记录持续保留</span></div></div><section class="card daily-task-card"><div class="section-title"><h3>每日待办列表</h3><span class="pill${rest?' rest-day-pill':''}">${selected===today()?'今天':esc(selected)}${rest?' · 休息日':''}</span></div><div class="toolbar"><div class="date-row"><button id="prevDay" aria-label="前一天" ${selected<=calendarDay?'disabled':''}>‹</button><input id="date" type="date" aria-label="计划日期" min="${calendarDay}" value="${selected}"><button id="nextDay" aria-label="后一天">›</button><button id="goToday">今天</button></div>${restDayButtonHTML()}<div class="segmented">${[['all','全部'],['pending','待完成'],['done','已完成']].map(([k,l])=>`<button data-filter="${k}" class="${filter===k?'selected':''}">${l}</button>`).join('')}</div></div><div class="progress"><i style="width:${tasks.length?done/tasks.length*100:0}%"></i></div><p class="task-timer-help">开始计时 → 暂停计时（休息） → 继续计时 → 左侧打钩完成。休息不计实际投入，刷新后仍保存。未点开始就完成，可后补起止时间自动计算；窄屏可左右滑动表格。</p>${dailyTaskContentHTML(shown)}<form id="quickAdd" class="add-form"><input name="title" required maxlength="200" aria-label="快速添加任务" placeholder="快速添加：今天想完成什么？"><input name="planned" type="number" value="30" min="1" max="600" aria-label="预计分钟"><input name="category" list="categories" value="其他" aria-label="任务分类" style="width:110px"><button class="primary">添加</button></form></section><div class="info-strip"><span>◷ 学期提醒：2026 年 12 月 14–20 日是本学期最后一周。</span><span>计划没有截止日，学期结束后继续使用。</span></div>`}
if(isCourseView())html=openedCourse?lessonsHTML():coursesHTML();
if(view==='journal'){const logged=all.filter(t=>t.day<calendarDay||t.done||t.actual||t.note||t.started_at||t.finished_at).sort((a,b)=>b.day.localeCompare(a.day)||b.id-a.id),recent=Array.from({length:7},(_,i)=>{const d=shift(today(),i-6),milliseconds=all.filter(t=>t.day===d).reduce((sum,t)=>sum+settledTaskMilliseconds(t),0);return {day:d,milliseconds,minutes:milliseconds/60000}}),max=Math.max(60,...recent.map(x=>x.minutes));html=hero('每一份投入，都有记录。','查看过去的全部任务，以及已记录的进展、用时和笔记。','<button id="export">导出记录 ↓</button>')+taskMoveNoticeHTML()+`<section class="card"><h3>最近 7 天实际投入（自动计算）</h3><div class="week-bars">${recent.map(x=>`<div class="bar" title="${formatTaskDuration(x.milliseconds)}"><span>${formatTaskMinutes(x.minutes)} 分</span><i style="height:${Math.max(3,x.minutes/max*80)}px"></i>${x.day.slice(5)}</div>`).join('')}</div></section><div class="table-scroll" style="margin-top:20px" tabindex="0" role="region" aria-label="任务记录表，可左右滚动"><table class="planner-table journal-table"><caption>历史任务记录 · ${logged.length} 项</caption><colgroup><col class="col-journal-day"><col class="col-journal-title"><col class="col-journal-category"><col class="col-journal-time"><col class="col-journal-time"><col class="col-journal-actual"><col class="col-journal-status"><col class="col-journal-note"><col class="col-journal-actions"></colgroup><thead><tr><th scope="col">日期</th><th scope="col">任务名称</th><th scope="col">分类</th><th scope="col">开始时间</th><th scope="col">结束时间</th><th scope="col">实际用时</th><th scope="col">状态</th><th scope="col">笔记</th><th scope="col">操作</th></tr></thead><tbody>${logged.map(t=>`<tr data-task-id="${t.id}"><td class="date-cell">${esc(t.day)}</td><th scope="row" class="task-name">${taskTitleHTML(t)}</th><td class="category-cell">${esc(t.category)}</td><td class="time-cell">${taskTimeHTML(t.started_at)}</td><td class="time-cell">${taskTimeHTML(t.finished_at)}</td><td class="number-cell">${actualHTML(t)}</td><td><span class="table-status ${taskStatus(t).style}">${taskStatus(t).label}</span></td><td class="note-cell">${t.note?`<button type="button" class="cell-open" data-edit="${t.id}" aria-label="查看 ${esc(t.title)} 的完整笔记"><span class="cell-preview">${esc(t.note)}</span><small class="cell-open-hint">查看完整笔记 →</small></button>`:'<span class="muted">—</span>'}</td><td class="table-actions"><button class="link" data-edit="${t.id}">编辑记录</button>${taskMoveButtonHTML(t)}</td></tr>`).join('')||'<tr><td colspan="9" class="table-empty">暂无过去的任务或已保存的学习记录。</td></tr>'}</tbody></table></div>`}
if(view==='settings')html=hero('把空间调整成适合自己的样子。','任务、课程和历史记录都属于自己的账号。')+`<div class="settings-grid"><form id="settingsForm" class="card"><h3>个人偏好</h3><label>称呼<input name="name" value="${esc(state.user.name)}" maxlength="40" required></label><label>每日参考目标（分钟）<input name="minutes" type="number" min="30" max="600" value="${state.user.minutes}" required></label><p class="muted">目标仅供参考，不会自动排课或生成任务。</p><button class="primary">保存设置</button></form>${avatarSettingsHTML()}<div><section class="card"><h3>数据备份</h3><p class="muted">导出任务、笔记和课程目录为 JSON。${isCloud()?'完整恢复需要备份云端私有 data/study.db；':'完整恢复需要备份 backend/study.db；'}当前没有 JSON 导入功能。</p><button id="export">导出数据 ↓</button></section></div></div><section class="card" style="margin-top:20px"><h3>当前运行方式</h3><p class="muted">${isCloud()?'云端版 · 电脑和平板登录同一网址和账号，使用同一份云端记录。视频和原始资料仍在本地移动硬盘，不随目录上传。':'F 盘本机版 · 资料来自移动硬盘，任务记录保存在本机。云端记录与本机记录各自独立；若使用云端版，请在各设备打开同一个云端网址。'}</p></section>`;
$('#content').innerHTML=html;renderedView=view;bind();restoreQuickAddDraft(carriedDraft,true);if(window.StudyTaskTimer)window.StudyTaskTimer.refresh();if(isLanguageView()&&!openedCourse){loadLanguagePractice();const listening=$('#listeningPractice');if(listening&&window.StudyListening&&typeof window.StudyListening.mount==='function')Promise.resolve(window.StudyListening.mount(listening,{language:currentCourseCategory(),username:state.user.username,api,audioBase:API})).catch(error=>toast(error.message||'听力练习暂时无法载入。'))}}
function syncTaskTimingForm(){
 const f=$('#taskForm'),start=f.elements.started_at.value,end=f.elements.finished_at.value,actual=f.elements.actual,output=$('#taskActualDuration');
 const milliseconds=key=>{const input=f.elements[key];return input.value?new Date(editing&&input.value===input.dataset.originalValue&&editing[key]?editing[key]:input.value).getTime():null};
 const startMs=milliseconds('started_at'),endMs=milliseconds('finished_at'),pauses=editing&&Array.isArray(editing.pause_intervals)?editing.pause_intervals:[],pauseOutput=$('#taskPauseDuration');
 $('#taskPauseSummary').hidden=!pauses.length;
 let error='';
 if(f.elements.started_at.validity.badInput||f.elements.finished_at.validity.badInput||(start&&!Number.isFinite(startMs))||(end&&!Number.isFinite(endMs)))error='请填写有效的开始和结束时间。';
 else if(end&&!start)error='请先填写开始时间。';
 else if(start&&end&&endMs<startMs)error='结束时间不能早于开始时间。';
 else if(start&&end&&Math.max(0,endMs-startMs-taskPauseMilliseconds(startMs,endMs,pauses))>86400000)error='单次任务的有效计时不能超过 24 小时，请分开记录。';
 f.elements.finished_at.setCustomValidity(error);
 actual.value=actual.dataset.originalValue||'0';
 output.classList.toggle('is-invalid',!!error);
 if(error){output.textContent='时间无效';output.dataset.status='invalid';pauseOutput.textContent='—';$('#taskTimingHint').textContent=error;return}
 if(start){const recorded=end?pauses:pauses.filter(interval=>interval&&interval.finished_at!==null);pauseOutput.textContent=formatTaskDuration(taskPauseMilliseconds(startMs,end?endMs:Date.now(),recorded))+(!end&&taskPaused(editing)?'（另有休息中）':'')}else pauseOutput.textContent='补齐开始时间后计算';
 if(start&&end){const active=Math.max(0,endMs-startMs-taskPauseMilliseconds(startMs,endMs,pauses));actual.value=Math.ceil(active/60000);output.textContent=formatTaskDuration(active);output.dataset.status='settled';$('#taskTimingHint').textContent='实际投入 = 结束时间 − 开始时间 − 暂停时长。修改起止时间后，自动扣除该范围内的暂停；时间按当前设备时区显示。'}
 else if(start&&!end){
  const legacyDone=editing&&editing.done&&!editing.finished_at&&f.elements.done.checked&&start===f.elements.started_at.dataset.originalValue;
  if(legacyDone){output.textContent=`${actual.value} 分钟（旧记录）`;output.dataset.status='legacy';$('#taskTimingHint').textContent='旧记录缺少结束时间，暂保留原有实际分钟；补齐起止时间后自动重算。'}
  else if(taskPaused(editing)){const now=Date.now(),active=Math.max(0,now-startMs-taskPauseMilliseconds(startMs,now,pauses));output.textContent=`已暂停 · ${formatTaskDuration(active)}`;output.dataset.status='paused';$('#taskTimingHint').textContent='已暂停，休息不计入实际投入。关闭本窗口后可在任务行点「继续计时」；勾选完成并保存也可直接结束任务。'}
  else{output.textContent='进行中，尚未结算';output.dataset.status='running';$('#taskTimingHint').textContent='实际投入 = 结束时间 − 开始时间 − 暂停时长。勾选完成并保存时自动记录结束时间，也可以补填结束时间。'}
 }else if(editing&&(Number(actual.value)>0||editing.done)){output.textContent=`${actual.value} 分钟（旧记录）`;output.dataset.status='legacy';$('#taskTimingHint').textContent='旧记录没有完整起止时间，原有实际分钟会保留。补填开始、结束时间后自动计算。'}
 else{output.textContent='等待开始 · 尚未记录';output.dataset.status='empty';$('#taskTimingHint').textContent='实际投入 = 结束时间 − 开始时间 − 暂停时长。开始后可暂停休息、继续计时；未开始就完成的任务，也可稍后补填起止时间。'}
}
function taskFormPayload(f){
 const d={title:f.elements.title.value,day:f.elements.day.value,category:f.elements.category.value,planned:+f.elements.planned.value,actual:+f.elements.actual.value,note:f.elements.note.value,done:f.elements.done.checked};
 ['started_at','finished_at'].forEach(key=>{const input=f.elements[key];if(input.value!==input.dataset.originalValue)d[key]=input.value?new Date(input.value).toISOString():null});
 return d
}
function openTask(task=null,proposal={}){editing=task;const f=$('#taskForm');f.reset();$('#taskDialogTitle').textContent=task?'编辑任务与记录':'新建任务';const data=task||{title:'',day:normaliseMainDay(),category:'其他',planned:30,actual:0,note:'',done:false,...proposal};['title','day','category','planned','actual','note'].forEach(k=>f.elements[k].value=data[k]);f.elements.actual.dataset.originalValue=String(data.actual==null?0:data.actual);['started_at','finished_at'].forEach(key=>{const input=f.elements[key];input.value=localDateTime(data[key]);input.dataset.originalValue=input.value});f.elements.done.checked=!!data.done;$('#taskProgress').hidden=false;syncTaskTimingForm();$('#taskDialog').showModal()}

function openCourse(c=null){courseEditing=c;const f=$('#courseForm');f.reset();$('#courseDialogTitle').textContent=c?'编辑课程':'添加课程';['title','url','description','source'].forEach(k=>f.elements[k].value=c?c[k]:(k==='source'?'user':''));f.elements.category.value=c?courseCategory(c):currentCourseCategory();$('#courseDialog').showModal()}
function bind(){const on=(s,e,fn)=>{const el=$(s);if(el)el.addEventListener(e,fn)},mutate=async(path,method,data,message)=>{if(dayRestBusy&&(path==='/tasks'||path.startsWith('/tasks/'))){toast('正在更新休息日，请稍候。');return false}try{await api(path,method,data);await refresh();toast(message);return true}catch(e){toast(e.message);return false}};syncDailyControls();
on('#avatarFile','change',selectAvatar);on('#avatarForm','submit',saveAvatar);on('#removeAvatar','click',()=>{if(!avatarBusy){avatarSelection++;avatarDraft='';updateAvatarControls()}});on('#cancelAvatar','click',()=>{if(!avatarBusy){avatarSelection++;avatarDraft=null;updateAvatarControls()}});updateAvatarControls();bindLanguagePractice();
on('#restDayTasks','toggle',event=>{const details=event.currentTarget;if(!details.isConnected)return;const day=details.dataset.day;if(details.open)restTaskExpanded.add(day);else restTaskExpanded.delete(day)});on('#restDayToggle','click',toggleRestDay);on('#newTask','click',()=>{if(!dayRestBusy)openTask()});on('#date','change',e=>{if(!dayRestBusy&&e.target.value){const day=normaliseMainDay(e.target.value);if(day===selected){e.target.value=day;return}selected=day;render()}});on('#prevDay','click',()=>{if(!dayRestBusy){selected=normaliseMainDay(shift(normaliseMainDay(),-1));render()}});on('#nextDay','click',()=>{if(!dayRestBusy){selected=normaliseMainDay(shift(normaliseMainDay(),1));render()}});on('#goToday','click',()=>{if(!dayRestBusy){selected=today();render()}});document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{if(!dayRestBusy){filter=b.dataset.filter;render()}});document.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>{if(!dayRestBusy)openTask(state.tasks.find(t=>t.id===+b.dataset.edit))});
on('#viewMovedTaskDay','click',viewMovedTaskDay);
document.querySelectorAll('[data-task-move]').forEach(button=>button.onclick=()=>openTaskMove(button.dataset.taskMove));
document.querySelectorAll('[data-task-reset]').forEach(b=>b.onclick=()=>resetTaskTimer(b.dataset.taskReset));
document.querySelectorAll('[data-task-action]').forEach(b=>b.onclick=async()=>{const id=String(Number(b.dataset.taskId)),action=b.dataset.taskAction;if(!taskMutationAllowed()||!['start','pause','resume'].includes(action)||taskActionWrites.has(id))return;taskActionWrites.add(id);syncDailyControls();try{await mutate('/tasks/'+id,'PATCH',{action},{start:'已开始计时，休息时可暂停',pause:'已暂停，休息时间不计入实际投入',resume:'已继续计时'}[action])}finally{taskActionWrites.delete(id);syncDailyControls()}});
document.querySelectorAll('[data-toggle]').forEach(b=>b.onchange=async()=>{const checked=b.checked,id=String(Number(b.dataset.toggle));if(!taskMutationAllowed()||taskActionWrites.has(id)){b.checked=!checked;return}taskActionWrites.add(id);syncDailyControls();try{const ok=await mutate('/tasks/'+id,'PATCH',{done:checked},checked?'已完成，继续下一项！':'已恢复为待完成');if(!ok&&b.isConnected)b.checked=!checked}finally{taskActionWrites.delete(id);syncDailyControls()}});
document.querySelectorAll('[data-delete]').forEach(b=>b.onclick=async()=>{const id=String(Number(b.dataset.delete));if(!taskMutationAllowed()||taskActionWrites.has(id)||!confirm('删除这项任务及其记录？'))return;taskActionWrites.add(id);syncDailyControls();try{await mutate('/tasks/'+id,'DELETE',null,'任务已移除')}finally{taskActionWrites.delete(id);syncDailyControls()}});on('#quickAdd','submit',async e=>{e.preventDefault();if(!taskMutationAllowed())return;const f=e.target,submitted={title:f.elements.title.value,day:normaliseMainDay(),category:f.elements.category.value,planned:+f.elements.planned.value};selected=submitted.day;const saved=await mutate('/tasks','POST',submitted,'任务已添加');const current=$('#quickAdd');if(saved&&current&&selected===submitted.day&&['title','planned','category'].every(k=>current.elements[k].value===String(submitted[k])))current.reset()});on('#settingsForm','submit',e=>{e.preventDefault();mutate('/settings','PATCH',{name:e.target.elements.name.value,minutes:+e.target.elements.minutes.value},'设置已保存')});
on('#refreshStorage','click',async()=>{const button=$('#refreshStorage');if(button)button.disabled=true;try{await refresh();toast(isCloud()?'课程目录已刷新':'硬盘状态已刷新')}catch(e){toast(e.message);if(button&&button.isConnected)button.disabled=false}});on('#newCourse','click',()=>openCourse());document.querySelectorAll('[data-course-guide]').forEach(b=>b.onclick=()=>openCourseGuide(state.courses.find(c=>c.id===+b.dataset.courseGuide)));document.querySelectorAll('[data-course-filter]').forEach(b=>b.onclick=()=>{courseFilter=b.dataset.courseFilter;render()});document.querySelectorAll('[data-edit-course]').forEach(b=>b.onclick=()=>openCourse(state.courses.find(c=>c.id===+b.dataset.editCourse)));document.querySelectorAll('[data-remove-course]').forEach(b=>b.onclick=()=>{if(confirm('从课程库移除这门课程？原始 G 盘文件不会删除。'))mutate('/courses/'+b.dataset.removeCourse,'DELETE',null,'课程已移除，原始文件保留')});document.querySelectorAll('[data-plan-course]').forEach(b=>b.onclick=()=>{const c=state.courses.find(c=>c.id===+b.dataset.planCourse);openTask(null,{title:('学习：'+c.title).slice(0,200),category:courseCategory(c)==='ai'?'学习':languageLabel(courseCategory(c)),note:c.url||''})});
document.querySelectorAll('[data-lessons]').forEach(b=>b.onclick=async()=>{if(!languageCanLeave())return;const token=++lessonLoadToken,courseId=+b.dataset.lessons;b.disabled=true;try{const d=await api('/courses/'+courseId+'/lessons');if(token!==lessonLoadToken)return;openedCourse=courseId;const course=state.courses.find(c=>c.id===courseId);if(course&&d.course)Object.assign(course,d.course);lessons=d.lessons;lessonQuery='';render()}catch(e){toast(e.message)}finally{if(b.isConnected)b.disabled=false}});document.querySelectorAll('[data-lesson-done]').forEach(b=>b.onchange=()=>saveLessonCompletion(b));on('#backCourses','click',()=>{lessonLoadToken++;openedCourse=null;lessons=[];render()});on('#lessonSearch','change',e=>{lessonQuery=e.target.value;render()});on('#lessonSearch','keydown',e=>{if(e.key==='Enter'){e.preventDefault();lessonQuery=e.currentTarget.value;render()}});document.querySelectorAll('[data-plan-lesson]').forEach(b=>b.onclick=()=>{const l=lessons.find(l=>l.id===b.dataset.planLesson),c=state.courses.find(c=>c.id===openedCourse);openTask(null,{title:l.title.slice(0,200),category:courseCategory(c)==='ai'?'学习':languageLabel(courseCategory(c)),note:'课程：'+c.title+'\n章节：'+l.section})});on('#lessonVideo','error',()=>toast('暂时无法读取视频，请确认移动硬盘仍已连接，并刷新硬盘状态。'));document.querySelectorAll('[data-play]').forEach(b=>b.onclick=()=>{const c=state.courses.find(c=>c.id===openedCourse);if(!localReadable(c)){toast(isCloud()?'视频保留在本地移动硬盘，请使用电脑本机版播放。':'请连接移动硬盘，再刷新硬盘状态。');return}const l=lessons.find(l=>l.id===b.dataset.play);$('#playerArea').hidden=false;$('#playingTitle').textContent=l.title;$('#lessonVideo').src=API+'/materials/'+encodeURIComponent(l.id);$('#lessonVideo').load();$('#playerArea').scrollIntoView({behavior:'smooth',block:'center'})});
on('#export','click',()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(state,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='人工智能学习计划记录-'+today()+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)})}
let authReady=false,authBusy=false;
function authPreference(key,value){try{if(arguments.length===1)return localStorage.getItem(key);if(value===null)localStorage.removeItem(key);else localStorage.setItem(key,value)}catch{}return null}
function syncPasswordPreference(){const f=$('#authForm');f.elements.password.autocomplete=registering?'new-password':f.elements.remember_password.checked?'current-password':'off'}
function rememberLoginChoice(username,remember){authPreference('study.remember-password',remember?'1':null);authPreference('study.remembered-username',remember?username:null)}
async function offerBrowserPassword(username,password){
 if(!window.isSecureContext||typeof window.PasswordCredential!=='function'||!navigator.credentials||typeof navigator.credentials.store!=='function')return false;
 try{await navigator.credentials.store(new window.PasswordCredential({id:username,password}));return true}catch{return false}
}
async function startLoginPage(){
 const f=$('#authForm');f.elements.remember_password.checked=authPreference('study.remember-password')==='1';
 if(f.elements.remember_password.checked)f.elements.username.value=authPreference('study.remembered-username')||'';
 f.elements.auto_login.checked=false;syncPasswordPreference();
 try{const result=await api('/session/start','POST');if(result.automatic===true)await refresh()}
 catch(e){$('#authError').textContent=e.message==='Failed to fetch'?(location.protocol==='https:'?'暂时无法连接服务，请稍后重试。':'后端尚未启动，请在 PyCharm 运行 start.py。'):e.message}
 finally{authReady=true;$('#authSubmit').disabled=false}
}
$('#switchAuth').onclick=()=>{registering=!registering;$('#nameField').hidden=!registering;$('#authTitle').textContent=registering?'创建你的计划空间':'欢迎回来';$('#authSubmit').textContent=registering?'创建账号 →':'登录计划空间 →';$('#switchAuth').textContent=registering?'已有账号？去登录':'还没有账号？创建计划空间';$('#authError').textContent='';syncPasswordPreference()};
$('#rememberPassword').onchange=()=>{const f=$('#authForm');rememberLoginChoice(f.elements.username.value.trim(),f.elements.remember_password.checked);syncPasswordPreference()};
$('#authForm').onsubmit=async e=>{e.preventDefault();if(!authReady||authBusy)return;authBusy=true;$('#authSubmit').disabled=true;$('#authError').textContent='';const f=e.target,username=f.elements.username.value.trim(),password=f.elements.password.value,remember=f.elements.remember_password.checked;try{await api(registering?'/register':'/login','POST',{username,password,name:f.elements.name.value,auto_login:f.elements.auto_login.checked});rememberLoginChoice(username,remember);await refresh();f.elements.password.value='';if(remember&&!await offerBrowserPassword(username,password))toast('已登录。若要记住密码，请在浏览器的保存密码提示中选择保存。')}catch(e){$('#authError').textContent=e.message==='Failed to fetch'?(location.protocol==='https:'?'网络连接失败，请稍后重试。':'连接失败，请在 PyCharm 运行 start.py。'):e.message}finally{authBusy=false;$('#authSubmit').disabled=false}};
document.querySelectorAll('nav button').forEach(b=>b.onclick=async()=>{if(!languageCanLeave())return;document.querySelectorAll('#languagePractice input,#languagePractice textarea,#languagePractice select,#languagePractice button').forEach(control=>control.disabled=true);const navigation=++navigationGeneration,previous={view,openedCourse,lessons,lessonQuery,courseFilter};view=b.dataset.view;if(view!==previous.view){lessonLoadToken++;openedCourse=null;lessons=[];lessonQuery='';courseFilter='all'}if(isLanguageView()){const cached=languageCache(false);if(cached&&!languageCacheDirty(cached))cached.loaded=false}try{const applied=await refresh();if(!applied&&navigation===navigationGeneration){view=renderedView;openedCourse=previous.openedCourse;lessons=previous.lessons;lessonQuery=previous.lessonQuery;courseFilter=previous.courseFilter;render()}}catch(e){if(navigation===navigationGeneration){view=renderedView;openedCourse=previous.openedCourse;lessons=previous.lessons;lessonQuery=previous.lessonQuery;courseFilter=previous.courseFilter;render();toast(e.message)}}});$('#logout').onclick=async()=>{if(!languageCanLeave())return;try{await api('/logout','POST');if(window.StudyListening&&typeof window.StudyListening.dispose==='function')window.StudyListening.dispose();resetAvatarDraft();languageCaches.clear();languageOwner='';state=null;$('#app').hidden=true;$('#auth').hidden=false;$('#authForm').elements.auto_login.checked=false;$('#authForm').elements.password.value='';try{if(navigator.credentials&&typeof navigator.credentials.preventSilentAccess==='function')await navigator.credentials.preventSilentAccess()}catch{}}catch(e){toast(e.message)}};
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible'){rolloverMainDay();scheduleMainDayBoundary()}if(canAutoRefresh())refresh({background:true}).catch(e=>{if(state&&document.visibilityState==='visible')toast(e.message)})});
$('#taskForm').elements.started_at.oninput=syncTaskTimingForm;$('#taskForm').elements.finished_at.oninput=syncTaskTimingForm;$('#taskForm').elements.done.onchange=syncTaskTimingForm;
$('#cancelDialog').onclick=()=>$('#taskDialog').close();$('#cancelCourse').onclick=()=>$('#courseDialog').close();
$('#taskForm').onsubmit=async e=>{e.preventDefault();if(dayRestBusy){toast('正在更新休息日，请稍候再保存任务。');return}const f=e.target;syncTaskTimingForm();if(!f.reportValidity())return;const submit=f.querySelector('[type="submit"]');submit.disabled=true;try{const d=taskFormPayload(f);await api(editing?'/tasks/'+editing.id:'/tasks',editing?'PATCH':'POST',d);selected=normaliseMainDay(d.day);if(view==='today'&&d.day<today())view='journal';$('#taskDialog').close();await refresh();toast('任务已保存')}catch(e){toast(e.message)}finally{submit.disabled=false}};
$('#courseForm').onsubmit=async e=>{e.preventDefault();const f=e.target,submit=f.querySelector('[type="submit"]');submit.disabled=true;try{await api(courseEditing?'/courses/'+courseEditing.id:'/courses',courseEditing?'PATCH':'POST',{title:f.elements.title.value,url:f.elements.url.value,description:f.elements.description.value,source:f.elements.source.value,category:f.elements.category.value});$('#courseDialog').close();await refresh();toast('课程已保存')}catch(e){toast(e.message)}finally{submit.disabled=false}};
startLoginPage();
scheduleMainDayBoundary();
window.addEventListener('pageshow',()=>{rolloverMainDay();scheduleMainDayBoundary()});

window.addEventListener('beforeunload',event=>{if(languageHasDirty()||languageWrites.size||languageBatch){event.preventDefault();event.returnValue=''}});
