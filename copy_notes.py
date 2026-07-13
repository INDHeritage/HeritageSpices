import os, shutil  
base_dir = r'C:\Users\shada\OneDrive\Desktop\SSC+Notes\static'  
dest_dir = r'd:\Company\Heritage web\static\notes'  
global_counter = 1  
chapters = [f'Sci1_Ch{i}' for i in range(1, 11)] + [f'Sci2_Ch{i}' for i in range(1, 11)]  
for ch in chapters:  
    ch_path = os.path.join(base_dir, ch)  
    if not os.path.exists(ch_path): continue  
    pages = [f for f in os.listdir(ch_path) if f.startswith('page_') and f.endswith('.png')]  
    pages.sort(key=lambda x: int(x.split('_')[1].split('.')[0]))  
    for p in pages:  
        src = os.path.join(ch_path, p)  
        dst = os.path.join(dest_dir, f'page_{global_counter}.png')  
        shutil.copy2(src, dst)  
        global_counter += 1  
print(f'Total pages copied: {global_counter - 1}')  
