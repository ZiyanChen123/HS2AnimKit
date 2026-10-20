import os, zipfile
root=r'D:\game\HS2\HS2AnimKit'
src=os.path.join(root,'hs2_rig_kit')
out=os.path.join(root,'HS2RigKit_addon.zip')
with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    for base,dirs,files in os.walk(src):
        dirs[:]=[d for d in dirs if d!='__pycache__']
        for f in files:
            if f.endswith(('.pyc','.pyo')): continue
            p=os.path.join(base,f)
            rel=os.path.relpath(p,root).replace('\\','/')
            z.write(p,rel)
print(out, os.path.getsize(out))
