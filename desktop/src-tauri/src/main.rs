#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod model;
mod ssh;
use model::{Connection, ApiState};
use std::{collections::HashMap, path::PathBuf, sync::Mutex, time::Duration};
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder, AppHandle, State, Emitter};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_opener::OpenerExt;
use uuid::Uuid;

struct Live { port: u16, tunnel: Option<ssh::Tunnel>, generation: Uuid }
struct Data { rows: Vec<Connection>, live: HashMap<Uuid,Live>, pending: HashMap<Uuid,(String,String)> }
struct Store { path: PathBuf, hosts: PathBuf, data: Mutex<Data> }
fn entry(id: Uuid) -> Result<keyring::Entry,String> {
    keyring::Entry::new("com.dbaba.desktop", &id.to_string()).map_err(|_| "Could not access the OS keychain.".into())
}
fn lookup(d: &Data,id: Uuid) -> Result<Connection,String> {
    d.rows.iter().find(|r|r.id==id).cloned().ok_or("Connection not found.".into())
}
fn refresh(app: &AppHandle) -> tauri::Result<()> {
    let store = app.state::<Store>();
    let d = store.data.lock().unwrap();
    let menu = Menu::new(app)?;
    menu.append(&MenuItem::with_id(app,"manager","Connections",true,None::<&str>)?)?;
    for row in &d.rows {
        let title = format!("{} — {}",row.label,if d.live.contains_key(&row.id) {"Connected"} else {"Disconnected"});
        menu.append(&MenuItem::with_id(app,row.id.to_string(),title,true,None::<&str>)?)?;
    }
    menu.append(&MenuItem::with_id(app,"quit","Quit DBaba",true,None::<&str>)?)?;
    if let Some(tray)=app.tray_by_id("dbaba") { tray.set_menu(Some(menu))?; }
    Ok(())
}
fn manager_only(window:&tauri::WebviewWindow)->Result<(),String> {
    if window.label()=="main" {Ok(())} else {Err("Only the connection manager can do this.".into())}
}
#[tauri::command]
fn list(window: tauri::WebviewWindow, store: State<Store>) -> Result<Vec<(Connection,bool)>,String> {
    manager_only(&window)?;
    let d=store.data.lock().unwrap();
    Ok(d.rows.iter().map(|r|(r.clone(),d.live.contains_key(&r.id))).collect())
}
#[tauri::command]
fn add(window:tauri::WebviewWindow, app: AppHandle, store: State<Store>, label:String, port:u16, ssh:Option<model::Ssh>, key:String) -> Result<(),String> {
    manager_only(&window)?;
    let row=Connection{id:Uuid::new_v4(),label,port,ssh,seen:Default::default()};
    row.validate()?;
    if key.len()>4096 || key.chars().any(char::is_control) { return Err("Invalid API key.".into()); }
    let mut d=store.data.lock().unwrap();
    if !key.is_empty() { entry(row.id)?.set_password(&key).map_err(|_| "Could not save API key in the OS keychain.")?; }
    let id=row.id;
    d.rows.push(row);
    if let Err(e)=model::save(&store.path,&d.rows) {
        d.rows.pop(); if !key.is_empty() { let _=entry(id)?.delete_credential(); } return Err(e);
    }
    drop(d); let _=refresh(&app); Ok(())
}
fn disconnect_inner(app:&AppHandle,id:Uuid) {
    if let Some(w)=app.get_webview_window(&format!("server-{id}")) { let _=w.destroy(); }
    app.state::<Store>().data.lock().unwrap().live.remove(&id);
    let _=refresh(app); let _=app.emit("connections-changed",());
}
#[tauri::command]
fn disconnect(window:tauri::WebviewWindow,app:AppHandle,id:Uuid)->Result<(),String> { manager_only(&window)?; disconnect_inner(&app,id); Ok(()) }
#[tauri::command]
fn remove(window:tauri::WebviewWindow,app:AppHandle,store:State<Store>,id:Uuid)->Result<(),String> {
    manager_only(&window)?;
    disconnect_inner(&app,id);
    let mut d=store.data.lock().unwrap();
    let mut rows=d.rows.clone(); rows.retain(|r|r.id!=id);
    match entry(id)?.delete_credential() { Ok(())|Err(keyring::Error::NoEntry)=>(), _=>return Err("Could not remove API key from the OS keychain.".into()) }
    model::save(&store.path,&rows)?; d.rows=rows; d.pending.remove(&id); drop(d); let _=refresh(&app); Ok(())
}
#[tauri::command]
async fn fingerprint(window:tauri::WebviewWindow,app:AppHandle,id:Uuid)->Result<Option<String>,String> { manager_only(&window)?; fingerprint_inner(app,id).await }
async fn fingerprint_inner(app:AppHandle,id:Uuid)->Result<Option<String>,String> {
    tauri::async_runtime::spawn_blocking(move || {
        let store=app.state::<Store>();
        let row=lookup(&store.data.lock().unwrap(),id)?;
        let Some(s)=row.ssh else {return Ok(None)};
        if ssh::known(&s.host,&store.hosts) {return Ok(None)}
        let (line,fp)=ssh::scan(&s)?;
        store.data.lock().unwrap().pending.insert(id,(line,fp.clone()));
        Ok(Some(fp))
    }).await.map_err(|_| "Could not check fingerprint.")?
}
#[tauri::command]
fn accept(window:tauri::WebviewWindow,store:State<Store>,id:Uuid,fingerprint:String)->Result<(),String> {
    manager_only(&window)?;
    use std::io::Write;
    let mut d=store.data.lock().unwrap();
    let row=lookup(&d,id)?;
    let s=row.ssh.ok_or("Not an SSH connection.")?;
    let (line,fp)=d.pending.remove(&id).ok_or("Check the fingerprint again.")?;
    if fp!=fingerprint || ssh::known(&s.host,&store.hosts) {return Err("Check the fingerprint again.".into())}
    let mut f=std::fs::OpenOptions::new().create(true).append(true).open(&store.hosts).map_err(|_| "Could not save known host.")?;
    f.write_all(line.as_bytes()).and_then(|_|f.sync_all()).map_err(|_|"Could not save known host.".into())
}
#[tauri::command]
async fn connect(window:tauri::WebviewWindow,app:AppHandle,id:Uuid)->Result<(),String> { manager_only(&window)?; connect_inner(app,id).await }
async fn connect_inner(app:AppHandle,id:Uuid)->Result<(),String> {
    let store=app.state::<Store>();
    if let Some(w)=app.get_webview_window(&format!("server-{id}")) { let _=w.set_focus(); return Ok(()) }
    let row=lookup(&store.data.lock().unwrap(),id)?;
    let hosts=store.hosts.clone();
    let remote=row.port;
    let s=row.ssh.clone();
    let (port,mut tunnel)=tauri::async_runtime::spawn_blocking(move || match s {
        Some(s)=>ssh::start(&s,remote,&hosts).map(|(p,t)|(p,Some(t))),
        None=>Ok((remote,None))
    }).await.map_err(|_|"Could not open connection.")??;
    let healthy = wait_for_health(port).await;
    if let Some(t) = &mut tunnel {
        match t.0.try_wait() {
            Ok(Some(_)) => return Err(t.stopped_reason()),
            Err(_) => return Err("Could not check SSH.".into()),
            Ok(None) => ()
        }
    }
    if !healthy {
        return Err(format!("DBaba isn't running at port {remote} on that computer"));
    }
    let generation=Uuid::new_v4();
    {
        let mut d=store.data.lock().unwrap();
        lookup(&d,id)?;
        if d.live.contains_key(&id) { return Err("Connection already open.".into()); }
        d.live.insert(id,Live{port,tunnel,generation});
    }
    let a=app.clone();
    let b=app.clone();
    let result=WebviewWindowBuilder::new(&app,format!("server-{id}"),WebviewUrl::External(model::base(port)))
        .title(&row.label).incognito(true)
        .on_navigation(move |url| {
            if model::same_origin(url,port) { true } else {
                if model::external(url) { let _=a.opener().open_url(url.as_str(),None::<&str>); }
                false
            }
        })
        .on_new_window(move |url,_| {
            if model::external(&url) { let _=b.opener().open_url(url.as_str(),None::<&str>); }
            tauri::webview::NewWindowResponse::Deny
        }).build();
    let window=match result {Ok(w)=>w,Err(_)=>{disconnect_inner(&app,id);return Err("Could not open DBaba screen.".into())}};
    let a=app.clone();
    window.on_window_event(move |event| if matches!(event,tauri::WindowEvent::Destroyed) {
        a.state::<Store>().data.lock().unwrap().live.remove(&id); let _=refresh(&a); let _=a.emit("connections-changed",());
    });
    let _=refresh(&app); let _=app.emit("connections-changed",());
    tauri::async_runtime::spawn(poll(app,id,generation));
    Ok(())
}
async fn wait_for_health(port: u16) -> bool {
    let Ok(client) = reqwest::Client::builder().no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(2)).build() else { return false };
    let url = model::base(port).join("api/health").unwrap();
    tokio::time::timeout(Duration::from_secs(10), async {
        loop {
            if let Ok(response) = client.get(url.clone()).send().await {
                if response.status().is_success() {
                    if let Ok(body) = response.json::<serde_json::Value>().await {
                        if body.get("ok").and_then(|v| v.as_bool()) == Some(true) { return true; }
                    }
                }
            }
            tokio::time::sleep(Duration::from_millis(250)).await;
        }
    }).await.unwrap_or(false)
}
async fn poll(app:AppHandle,id:Uuid,generation:Uuid) {
    let Ok(client)=reqwest::Client::builder().no_proxy().redirect(reqwest::redirect::Policy::none()).timeout(Duration::from_secs(10)).build() else {return};
    loop {
        tokio::time::sleep(Duration::from_secs(60)).await;
        let port={
            let store=app.state::<Store>(); let mut d=store.data.lock().unwrap();
            let Some(live)=d.live.get_mut(&id) else {return};
            if live.generation!=generation {return}
            if let Some(t)=&mut live.tunnel {
                match t.0.try_wait() {
                    Ok(None) => (),
                    result => {
                        let reason = if result.is_ok() { t.stopped_reason() } else { "Could not check SSH.".into() };
                        drop(d);
                        disconnect_inner(&app,id);
                        let _=app.emit("connection-error",reason);
                        return;
                    }
                }
            }
            live.port
        };
        let mut req=client.get(model::base(port).join("api/state").unwrap());
        match entry(id).and_then(|e| match e.get_password() {
            Ok(k)=>Ok(Some(k)),Err(keyring::Error::NoEntry)=>Ok(None),Err(_)=>Err("Could not read API key.".into())
        }) {Ok(Some(k))=>req=req.bearer_auth(k),Ok(None)=>(),Err(_)=>continue}
        let Ok(response)=req.send().await else {continue};
        if !response.status().is_success() {continue}
        let Ok(state)=response.json::<ApiState>().await else {continue};
        let store=app.state::<Store>(); let mut d=store.data.lock().unwrap();
        if !d.live.get(&id).is_some_and(|l|l.generation==generation) {return}
        let mut rows=d.rows.clone();
        let Some(row)=rows.iter_mut().find(|r|r.id==id) else {return};
        let mut notifications=vec![];
        for alert in state.alerts {
            if model::eligible(&alert,&row.seen) {
                row.seen.insert(alert.id.clone()); notifications.push(alert);
            }
        }
        if !notifications.is_empty() && model::save(&store.path,&rows).is_ok() {
            d.rows=rows;
            for alert in notifications {
                let _=app.notification().builder().title(alert.title).body(alert.text).show();
            }
        }
    }
}
fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init()).plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![list,add,remove,connect,disconnect,fingerprint,accept])
        .setup(|app| {
            let dir=app.path().app_config_dir()?;
            std::fs::create_dir_all(&dir)?;
            let path=dir.join("connections.json");
            let rows=model::load(&path).map_err(std::io::Error::other)?;
            app.manage(Store{path,hosts:dir.join("known_hosts"),data:Mutex::new(Data{rows,live:HashMap::new(),pending:HashMap::new()})});
            let pixels=(0..32*32).flat_map(|i| {let x=i%32;let y=i/32;if x>3&&x<28&&y>3&&y<28 {[37,99,235,255]}else{[0,0,0,0]}}).collect();
            TrayIconBuilder::with_id("dbaba").icon(tauri::image::Image::new_owned(pixels,32,32))
                .tooltip("DBaba").on_menu_event(|app,event| match event.id().as_ref() {
                    "quit"=>{app.state::<Store>().data.lock().unwrap().live.clear();app.exit(0)},
                    "manager"=>{if let Some(w)=app.get_webview_window("main"){let _=w.show();let _=w.set_focus();}},
                    id=>if let Ok(id)=Uuid::parse_str(id) {
                        let a=app.clone();tauri::async_runtime::spawn(async move {
                            match fingerprint_inner(a.clone(),id).await {
                                Ok(None)=>{if let Err(e)=connect_inner(a.clone(),id).await {let _=a.emit("connection-error",e);}},
                                _=>{if let Some(w)=a.get_webview_window("main"){let _=w.show();let _=w.set_focus();}let _=a.emit("verify-host",id);}
                            }
                        });
                    }
                }).build(app)?;
            refresh(app.handle())?;
            if let Some(window)=app.get_webview_window("main") {
                let a=app.handle().clone();
                window.on_window_event(move |event| if matches!(event,tauri::WindowEvent::CloseRequested{..}) {
                    a.state::<Store>().data.lock().unwrap().live.clear();
                    a.exit(0);
                });
            }
            Ok(())
        }).build(tauri::generate_context!()).expect("Could not start DBaba desktop")
        .run(|app,event| match event {
            tauri::RunEvent::ExitRequested{..}|tauri::RunEvent::Exit=>{app.state::<Store>().data.lock().unwrap().live.clear();},
            _=>()
        });
}
