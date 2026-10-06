# Git & GitHub Rehberi

## 1. İlk kurulum (bir kez)

```bash
git config --global user.name "Murat UĞUR"
git config --global user.email "<github e-postanız>"
git config --global init.defaultBranch main
```

## 2. Repoyu klonlayıp çalışmak

```bash
git clone https://github.com/Murat-UGUR/divitrack.git
cd divitrack
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
make test
```

## 3. Günlük akış

```bash
git switch -c feature/tefas-entegrasyonu   # her iş için ayrı dal
# ... değişiklikler ...
make format && make test
git add -A
git status                                  # data/ veya *.db görünmemeli!
git commit -m "feat: TEFAS fon fiyatı sağlayıcısı"
git push -u origin feature/tefas-entegrasyonu
gh pr create --fill                         # GitHub CLI ile PR
```

Commit mesajları için [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`.

## 4. Sürüm etiketi

```bash
git switch main && git pull
git tag -a v0.1.0 -m "İlk sürüm"
git push origin v0.1.0
gh release create v0.1.0 --generate-notes
```

## 5. Gizlilik kontrolü

`.gitignore` kişisel verileri dışlar, yine de push öncesi:

```bash
git ls-files | grep -Ei '\.(db|sqlite|csv)$|^data/|\.env$'   # çıktı boş olmalı
```

Yanlışlıkla bir veri dosyası commit'lendiyse:

```bash
git rm --cached data/divitrack.db
git commit -m "chore: veri dosyasını takipten çıkar"
```

Dosya zaten push edildiyse geçmişten de silinmesi gerekir (`git filter-repo`) — ve içerik ifşa olmuş sayılmalıdır.

## 6. Sıfırdan yeni repo oluşturmak (referans)

```bash
cd divitrack
git init && git add -A && git commit -m "feat: ilk sürüm"
gh repo create Murat-UGUR/divitrack --public --source=. --push
```
