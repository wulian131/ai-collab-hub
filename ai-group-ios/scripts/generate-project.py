#!/usr/bin/env python3
"""Deterministic, dependency-free Xcode project for the native iPhone app."""
from pathlib import Path
import hashlib,json,plistlib,struct,zlib
root=Path(__file__).resolve().parents[1]
def key(s):return hashlib.sha256(s.encode()).hexdigest()[:24].upper()
def quote(s):return json.dumps(s,ensure_ascii=False)
project=root/'AIGroup.xcodeproj';project.mkdir(exist_ok=True)
assets=root/'AIGroup/Assets.xcassets';icons=assets/'AppIcon.appiconset';icons.mkdir(parents=True,exist_ok=True)
(assets/'Contents.json').write_text(json.dumps({'info':{'author':'xcode','version':1}}))
(icons/'Contents.json').write_text(json.dumps({'images':[{'filename':'AppIcon.png','idiom':'universal','platform':'ios','size':'1024x1024'}],'info':{'author':'xcode','version':1}},indent=2))
# Regenerate repository-owned hub icons; no external artwork.
import subprocess
subprocess.run(['python3',str(root.parent/'tools/generate-icons.py')],check=True)
info={'CFBundleDisplayName':'AI 协作','CFBundleExecutable':'$(EXECUTABLE_NAME)','CFBundleIdentifier':'$(PRODUCT_BUNDLE_IDENTIFIER)','CFBundleInfoDictionaryVersion':'6.0','CFBundleName':'$(PRODUCT_NAME)','CFBundlePackageType':'APPL','CFBundleShortVersionString':'$(MARKETING_VERSION)','CFBundleVersion':'$(CURRENT_PROJECT_VERSION)','LSRequiresIPhoneOS':True,'UIApplicationSceneManifest':{'UIApplicationSupportsMultipleScenes':False},'UILaunchScreen':{},'UISupportedInterfaceOrientations':['UIInterfaceOrientationPortrait','UIInterfaceOrientationLandscapeLeft','UIInterfaceOrientationLandscapeRight'],'ITSAppUsesNonExemptEncryption':False}
(root/'AIGroup/Info.plist').write_bytes(plistlib.dumps(info,sort_keys=False))
privacy={'NSPrivacyTracking':False,'NSPrivacyTrackingDomains':[], 'NSPrivacyAccessedAPITypes':[{'NSPrivacyAccessedAPIType':'NSPrivacyAccessedAPICategoryUserDefaults','NSPrivacyAccessedAPITypeReasons':['CA92.1']}], 'NSPrivacyCollectedDataTypes':[{'NSPrivacyCollectedDataType':t,'NSPrivacyCollectedDataTypeLinked':True,'NSPrivacyCollectedDataTypeTracking':False,'NSPrivacyCollectedDataTypePurposes':['NSPrivacyCollectedDataTypePurposeAppFunctionality']} for t in ['NSPrivacyCollectedDataTypeUserID','NSPrivacyCollectedDataTypeEmailsOrTextMessages','NSPrivacyCollectedDataTypePhotosorVideos','NSPrivacyCollectedDataTypeOtherUserContent']]}
(root/'AIGroup/PrivacyInfo.xcprivacy').write_bytes(plistlib.dumps(privacy,sort_keys=False))
files=sorted(root.glob('AIGroup/**/*.swift'))
objects=[];children=[];sourcebuild=[];resourcebuild=[]
for file in files+[assets,root/'AIGroup/PrivacyInfo.xcprivacy',root/'AIGroup/Info.plist']:
 rel=str(file.relative_to(root));ref=key(rel);children.append(ref)
 kind='folder.assetcatalog' if file==assets else 'text.xml' if file.suffix in ('.plist','.xcprivacy') else 'sourcecode.swift'
 objects.append(f'{ref} = {{isa = PBXFileReference; lastKnownFileType = {kind}; path = {quote(rel)}; sourceTree = SOURCE_ROOT;}};')
 if file in files or file==assets or file.suffix=='.xcprivacy':
  build=key('build:'+rel);objects.append(f'{build} = {{isa = PBXBuildFile; fileRef = {ref};}};');(sourcebuild if file in files else resourcebuild).append(build)
ids={s:key('project:'+s) for s in ['main','root','products','app','target','sources','resources','frameworks','project-config','target-config','pdebug','prelease','tdebug','trelease']}
objects += [f'{ids["app"]} = {{isa = PBXFileReference; explicitFileType = wrapper.application; path = AIGroup.app; sourceTree = BUILT_PRODUCTS_DIR;}};',f'{ids["root"]} = {{isa = PBXGroup; children = ({",".join(children+[ids["products"]])}); sourceTree = "<group>";}};',f'{ids["products"]} = {{isa = PBXGroup; name = Products; children = ({ids["app"]}); sourceTree = "<group>";}};']
for phase,items,isa in [('sources',sourcebuild,'PBXSourcesBuildPhase'),('resources',resourcebuild,'PBXResourcesBuildPhase'),('frameworks',[],'PBXFrameworksBuildPhase')]:objects.append(f'{ids[phase]} = {{isa = {isa}; buildActionMask = 2147483647; files = ({",".join(items)}); runOnlyForDeploymentPostprocessing = 0;}};')
objects.append(f'{ids["target"]} = {{isa = PBXNativeTarget; buildConfigurationList = {ids["target-config"]}; buildPhases = ({ids["sources"]},{ids["frameworks"]},{ids["resources"]}); buildRules = (); dependencies = (); name = AIGroup; productName = AIGroup; productReference = {ids["app"]}; productType = "com.apple.product-type.application";}};')
objects.append(f'{ids["main"]} = {{isa = PBXProject; attributes = {{LastUpgradeCheck = 1600; TargetAttributes = {{{ids["target"]} = {{CreatedOnToolsVersion = 16.0; ProvisioningStyle = Automatic;}};}};}}; buildConfigurationList = {ids["project-config"]}; compatibilityVersion = "Xcode 14.0"; developmentRegion = "zh-Hans"; hasScannedForEncodings = 0; knownRegions = ("zh-Hans",en,Base); mainGroup = {ids["root"]}; productRefGroup = {ids["products"]}; projectDirPath = ""; projectRoot = ""; targets = ({ids["target"]});}};')
for scope in ['p','t']:
 for config in ['debug','release']:
  settings={'CLANG_ENABLE_MODULES':'YES','SDKROOT':'iphoneos','IPHONEOS_DEPLOYMENT_TARGET':'17.0','SWIFT_VERSION':'5.0','SWIFT_OPTIMIZATION_LEVEL':'-Onone' if config=='debug' else '-O','DEBUG_INFORMATION_FORMAT':'dwarf' if config=='debug' else 'dwarf-with-dsym'} if scope=='p' else {'ASSETCATALOG_COMPILER_APPICON_NAME':'AppIcon','CODE_SIGN_STYLE':'Automatic','CURRENT_PROJECT_VERSION':'15','GENERATE_INFOPLIST_FILE':'NO','INFOPLIST_FILE':'AIGroup/Info.plist','MARKETING_VERSION':'0.6.4','PRODUCT_BUNDLE_IDENTIFIER':'org.example.aicollabhub.ios','PRODUCT_NAME':'$(TARGET_NAME)','TARGETED_DEVICE_FAMILY':'1','SUPPORTED_PLATFORMS':'iphoneos iphonesimulator','SWIFT_EMIT_LOC_STRINGS':'YES'}
  if scope=='p' and config=='debug':settings.update({'ENABLE_TESTABILITY':'YES','SWIFT_ACTIVE_COMPILATION_CONDITIONS':'DEBUG'})
  pairs=' '.join(f'{k} = {quote(v)};' for k,v in settings.items());objects.append(f'{ids[scope+config]} = {{isa = XCBuildConfiguration; buildSettings = {{{pairs}}}; name = {config.title()};}};')
 label='project-config' if scope=='p' else 'target-config';objects.append(f'{ids[label]} = {{isa = XCConfigurationList; buildConfigurations = ({ids[scope+"debug"]},{ids[scope+"release"]}); defaultConfigurationIsVisible = 0; defaultConfigurationName = Release;}};')
(project/'project.pbxproj').write_text('// !$*UTF8*$!\n{archiveVersion = 1; classes = {}; objectVersion = 56; objects = {\n'+'\n'.join(objects)+f'\n}}; rootObject = {ids["main"]};\n}}\n')
schemes=project/'xcshareddata/xcschemes';schemes.mkdir(parents=True,exist_ok=True)
ref=f'<BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{ids["target"]}" BuildableName="AIGroup.app" BlueprintName="AIGroup" ReferencedContainer="container:AIGroup.xcodeproj"/>'
(schemes/'AIGroup.xcscheme').write_text(f'''<?xml version="1.0" encoding="UTF-8"?><Scheme LastUpgradeVersion="1600" version="1.3"><BuildAction parallelizeBuildables="YES" buildImplicitDependencies="YES"><BuildActionEntries><BuildActionEntry buildForTesting="YES" buildForRunning="YES" buildForProfiling="YES" buildForArchiving="YES" buildForAnalyzing="YES">{ref}</BuildActionEntry></BuildActionEntries></BuildAction><TestAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" shouldUseLaunchSchemeArgsEnv="YES"/><LaunchAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" launchStyle="0" useCustomWorkingDirectory="NO" ignoresPersistentStateOnLaunch="NO" debugServiceExtension="internal" allowLocationSimulation="YES"><BuildableProductRunnable runnableDebuggingMode="0">{ref}</BuildableProductRunnable></LaunchAction><ProfileAction buildConfiguration="Release" shouldUseLaunchSchemeArgsEnv="YES" savedToolIdentifier="" useCustomWorkingDirectory="NO"><BuildableProductRunnable runnableDebuggingMode="0">{ref}</BuildableProductRunnable></ProfileAction><AnalyzeAction buildConfiguration="Debug"/><ArchiveAction buildConfiguration="Release" revealArchiveInOrganizer="YES"/></Scheme>''')
print('Generated native Xcode project:',len(files),'Swift files')
