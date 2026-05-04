import os
import glob
import re
from bs4 import BeautifulSoup

def process_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        html = f.read()

    # Find modals by locating <div class="modal-overlay" ...>
    modals = []
    
    idx = 0
    while True:
        match = re.search(r'<div[^>]*class=["\'][^"\']*modal-overlay[^"\']*["\'][^>]*>', html[idx:])
        if not match:
            # Also check if class is after id
            match2 = re.search(r'<div[^>]*id=["\'][^"\']+["\'][^>]*class=["\'][^"\']*modal-overlay[^"\']*["\'][^>]*>', html[idx:])
            if not match2:
                break
            match = match2
            
        start_tag_idx = idx + match.start()
        
        open_divs = 0
        end_idx = start_tag_idx
        
        # Iterating through all tags
        tag_regex = re.compile(r'</?div\b[^>]*>', re.IGNORECASE)
        for tag_match in tag_regex.finditer(html[start_tag_idx:]):
            tag_text = tag_match.group(0).lower()
            if tag_text.startswith('</div'):
                open_divs -= 1
            else:
                open_divs += 1
                
            if open_divs == 0:
                end_idx = start_tag_idx + tag_match.end()
                break
                
        modal_html = html[start_tag_idx:end_idx]
        
        # Parse with BS4 to extract parts
        soup = BeautifulSoup(modal_html, 'html.parser')
        overlay = soup.find('div')
        if not overlay or 'modal-overlay' not in overlay.get('class', []):
            idx = end_idx
            continue
            
        modal_id = overlay.get('id', '')
        
        modal_box = overlay.find(class_=re.compile(r'\bmodal-box\b'))
        if not modal_box:
            idx = end_idx
            continue
            
        custom_class = 'wide' if 'wide' in modal_box.get('class', []) else ''
        
        hdr = modal_box.find(class_=re.compile(r'\bmodal-hdr\b'))
        title_str = ""
        if hdr:
            h2 = hdr.find('h2')
            if h2:
                h2_id = h2.get('id')
                h2_content = "".join([str(c) for c in h2.contents]).strip()
                if h2_id:
                    title_str = f'<span id="{h2_id}">{h2_content}</span>'
                else:
                    title_str = h2_content
            hdr.extract() # Remove header
            
        form = modal_box.find('form')
        
        # Find footer
        footer_str = ""
        buttons_divs = modal_box.find_all('div', recursive=True)
        footer_div = None
        for div in reversed(buttons_divs):
            text = div.get_text().lower()
            if ('batal' in text or 'tutup' in text) and div.find('button'):
                # We have found a footer candidate
                footer_div = div
                break
                
        if footer_div:
            if form:
                form_id = form.get('id')
                if form_id:
                    for btn in footer_div.find_all('button'):
                        b_type = btn.get('type', '').lower()
                        is_submit = False
                        if b_type == 'submit':
                            is_submit = True
                        elif b_type == '':
                            b_text = btn.get_text().lower()
                            if 'simpan' in b_text or 'tambah' in b_text or 'buat' in b_text or 'ya' in b_text or 'lanjut' in b_text:
                                is_submit = True
                                btn['type'] = 'submit'
                            else:
                                btn['type'] = 'button'
                        
                        if is_submit:
                            btn['form'] = form_id
            footer_str = "".join([str(c) for c in footer_div.contents]).strip()
            footer_div.extract()
            
        body_str = "".join([str(c) for c in modal_box.contents]).strip()
        
        modals.append({
            'id': modal_id,
            'start': start_tag_idx,
            'end': end_idx,
            'title': title_str,
            'body': body_str,
            'footer': footer_str,
            'customClass': custom_class
        })
        
        idx = end_idx
        
    if not modals:
        print(f"No modals found in {filepath}")
        return
        
    # Remove modals from HTML in reverse order
    for m in reversed(modals):
        html = html[:m['start']] + html[m['end']:]
        
    # Generate the script injection
    script_lines = []
    script_lines.append("const MODAL_TEMPLATES = {")
    for m in modals:
        t = m['title'].replace('\\', '\\\\').replace('`', '\\`').replace('$', '\\$')
        b = m['body'].replace('\\', '\\\\').replace('`', '\\`').replace('$', '\\$')
        f = m['footer'].replace('\\', '\\\\').replace('`', '\\`').replace('$', '\\$')
        c = m['customClass']
        
        script_lines.append(f'  "{m["id"]}": {{')
        script_lines.append(f'    title: `{t}`,')
        script_lines.append(f'    body: `{b}`,')
        script_lines.append(f'    footer: `{f}`,')
        script_lines.append(f'    customClass: "{c}"')
        script_lines.append("  },")
    script_lines.append("};")
    script_lines.append("document.addEventListener('DOMContentLoaded', () => {")
    script_lines.append("  for (const [id, m] of Object.entries(MODAL_TEMPLATES)) {")
    script_lines.append("    if (!document.getElementById(id)) {")
    script_lines.append("      UI.showModal(id, m.title, m.body, m.footer, m.customClass);")
    script_lines.append("      const el = document.getElementById(id);")
    script_lines.append("      if (el) el.style.display = 'none';")
    script_lines.append("    }")
    script_lines.append("  }")
    script_lines.append("  document.body.style.overflow = '';")
    script_lines.append("});")
    
    script_tag = "\n<script>\n" + "\n".join(script_lines) + "\n</script>\n"
    
    if "</body>" in html:
        html = html.replace("</body>", script_tag + "</body>")
    else:
        html += script_tag
        
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html)
        
if __name__ == '__main__':
    print("Testing on frontend/items.html")
    process_file("frontend/items.html")
