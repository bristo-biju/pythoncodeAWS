pipeline {
    agent any

    environment {
        IMAGE_NAME     = "student-app"
        CONTAINER_NAME = "student-app-container"
    }

    stages {
        stage('Checkout') {
            steps { checkout scm }
        }

        stage('Validate deployment config') {
            steps {
                sh '''
                    set +x
                    : "${DB_HOST:?Set DB_HOST in Jenkins credentials or environment}"
                    : "${DB_NAME:?Set DB_NAME in Jenkins environment}"
                    : "${DB_SECRET_ARN:?Set DB_SECRET_ARN in Jenkins environment}"
                    : "${S3_BUCKET:?Set S3_BUCKET in Jenkins environment}"
                    : "${AWS_DEFAULT_REGION:?Set AWS_DEFAULT_REGION in Jenkins environment}"
                '''
            }
        }

        stage('Build image') {
            steps { sh 'docker build -t $IMAGE_NAME .' }
        }

        stage('Stop old container') {
            steps { sh 'docker rm -f $CONTAINER_NAME || true' }
        }

        stage('Run new container') {
            steps {
                sh '''
                    set +x
                    docker run -d --name "$CONTAINER_NAME" -p 5020:8000 \
                        --env DB_HOST --env DB_PORT --env DB_NAME --env DB_SECRET_ARN \
                        --env S3_BUCKET --env AWS_DEFAULT_REGION \
                        "$IMAGE_NAME"
                '''
            }
        }
    }
}
