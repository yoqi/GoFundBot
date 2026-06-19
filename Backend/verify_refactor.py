from selenium import webdriver
from selenium.webdriver.chrome.options import Options

options = Options()
options.add_argument('--headless')  # 无头模式，不显示浏览器窗口
driver = webdriver.Chrome(options=options)
driver.get("你的完整API链接")
# 获取页面内容
content = driver.page_source
driver.quit()