<?php
// PHP CLI with cURL: php examples/local_contract.php GROUP
$base=rtrim(getenv('DBABA_URL') ?: 'http://127.0.0.1:8765','/');
if (!preg_match('/^http:\/\/127\.0\.0\.1:([0-9]{1,5})$/D',$base,$m) || (int)$m[1]<1 || (int)$m[1]>65535) exit(2);
$ch=curl_init();
curl_setopt_array($ch,[CURLOPT_RETURNTRANSFER=>true,CURLOPT_COOKIEFILE=>'',CURLOPT_PROXY=>'',CURLOPT_FOLLOWLOCATION=>false,CURLOPT_TIMEOUT=>30]);
function api($path,$method='GET',$body=null,$setup=false,$html=false){
 global $ch,$base;
 $headers=['Content-Type: application/json'];
 if($setup)$headers[]='X-DBaba-Setup: '.getenv('DBABA_SETUP_TOKEN');
 elseif(getenv('DBABA_API_KEY'))$headers[]='Authorization: Bearer '.getenv('DBABA_API_KEY');
 curl_setopt_array($ch,[CURLOPT_URL=>$base.$path,CURLOPT_POSTFIELDS=>$body===null?null:json_encode($body,JSON_THROW_ON_ERROR),CURLOPT_CUSTOMREQUEST=>$method,CURLOPT_HTTPHEADER=>$headers]);
 $response=curl_exec($ch);$status=curl_getinfo($ch,CURLINFO_RESPONSE_CODE);
 if($response===false||$status<200||$status>=300)throw new RuntimeException('Local HTTP error');
 return $html?$response:json_decode($response,true,512,JSON_THROW_ON_ERROR);
}
function show($v){echo json_encode($v,JSON_THROW_ON_ERROR).PHP_EOL;}
function ident($name){$v=getenv($name);if(!preg_match('/^[A-Za-z0-9_-]{1,128}$/D',$v?:''))throw new RuntimeException('Invalid ID');return $v;}
function save_private($path,$data){$old=umask(0077);try{$f=fopen($path,'xb');if(!$f)throw new RuntimeException('Cannot create file');try{if(fwrite($f,$data)!==strlen($data))throw new RuntimeException('Cannot write file');}finally{fclose($f);}}finally{umask($old);}}
try{
 switch($argv[1]??''){
 case 'health':show(api('/api/health'));break;
 case 'state':show(api('/api/state'));break;
 case 'chat':api('/api/new-chat','POST');echo api('/api/message','POST',['text'=>'How do indexes work?'])['reply'].PHP_EOL;break;
 case 'alerts':show(api('/api/alerts/'.ident('DBABA_ALERT_ID').'/ack','POST'));break;
 case 'report':show(api('/api/report'));show(api('/api/report/scan','POST'));save_private('report.html',api('/api/report.html','GET',null,false,true));break;
 case 'jobs':show(api('/api/jobs'));show(api('/api/jobs/'.ident('DBABA_JOB_ID'),'POST',['enabled'=>true,'at'=>'09:30']));break;
 case 'setup':
 $body=json_decode(stream_get_contents(STDIN),true,512,JSON_THROW_ON_ERROR);$test=$body;unset($test['screen_password']);
 show(api('/api/setup/detect','POST',null,true));
 if(!api('/api/setup/test','POST',$test,true)['ok'])throw new RuntimeException('Test failed');
 $result=api('/api/setup/save','POST',$body,true);if(!$result['ok'])throw new RuntimeException('Setup failed');
 save_private('dbaba-key',$result['api_key']);echo "Setup saved; key stored locally.\n";break;
 case 'session':$body=json_decode(stream_get_contents(STDIN),true,512,JSON_THROW_ON_ERROR);api('/api/login','POST',['password'=>$body['password']]);show(api('/api/state'));api('/api/logout','POST');break;
 case 'reset':if(getenv('DBABA_CONFIRM_RESET')!=='yes')throw new RuntimeException('Confirm reset');show(api('/api/setup/reset','POST'));break;
 default:throw new RuntimeException('Unknown group');
 }
}catch(Throwable $e){fwrite(STDERR,"Local request failed; check configuration and credentials.\n");exit(1);}finally{curl_close($ch);}
