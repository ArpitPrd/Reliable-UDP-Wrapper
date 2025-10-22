## Getting around with git

- git fetch upstream
- git rebase upstream/main

    ```
    # once at start
    git clone https://github.com/yourusername/Assignment4.git
    cd Assignment4
    git remote add upstream https://github.com/professor/Assignment4.git

    # while working
    git checkout -b my-solution
    # ...edit code...
    git add .
    git commit -m "Implemented timeout retransmission logic"
    git push origin my-solution

    # later when prof updates repo
    git fetch upstream
    git checkout main
    git merge upstream/main
    git checkout my-solution
    git rebase main
    git push origin my-solution

    ```